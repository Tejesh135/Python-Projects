import argparse
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd
import pdfplumber
from PyPDF2 import PdfReader


LOGGER = logging.getLogger("invoice_extractor")


@dataclass
class InvoiceRecord:
    file_name: str
    file_path: str
    vendor_name: Optional[str]
    invoice_date: Optional[str]
    subtotal: Optional[Decimal]
    tax: Optional[Decimal]
    total: Optional[Decimal]
    anomaly_count: int
    anomalies: str


@dataclass
class InvoiceIssue:
    file_name: str
    file_path: str
    issue_type: str
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract vendor, date, subtotal, tax, and total from invoice PDFs, "
            "then output a cleaned CSV and an anomaly report."
        )
    )
    parser.add_argument(
        "--input-folder",
        "-i",
        required=True,
        help="Path to folder containing invoice PDFs.",
    )
    parser.add_argument(
        "--output-csv",
        "-o",
        default="cleaned_invoices.csv",
        help="Path to output cleaned CSV file.",
    )
    parser.add_argument(
        "--error-report",
        "-e",
        default="invoice_error_report.csv",
        help="Path to output anomaly/error CSV file.",
    )
    parser.add_argument(
        "--log-file",
        "-l",
        default="invoice_extractor.log",
        help="Path to log file.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan PDF files recursively in subfolders.",
    )
    return parser.parse_args()


def configure_logging(log_file: str) -> None:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def list_pdf_files(folder: Path, recursive: bool) -> List[Path]:
    if recursive:
        return sorted(folder.rglob("*.pdf"))
    return sorted(folder.glob("*.pdf"))


def extract_text_from_pdf(pdf_path: Path) -> str:
    page_texts: List[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as plumber_error:
        LOGGER.warning(
            "pdfplumber failed for '%s': %s. Falling back to PyPDF2.",
            pdf_path.name,
            plumber_error,
        )
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            page_texts.append(page.extract_text() or "")

    return "\n".join(page_texts).strip()


def sanitize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def find_first_labeled_value(text: str, labels: Iterable[str]) -> Optional[str]:
    escaped_labels = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"(?im)^\s*(?:{escaped_labels})\b\s*[:\-]\s*(.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None
    return sanitize_text(match.group(1))


def parse_vendor_name(text: str) -> Optional[str]:
    vendor = find_first_labeled_value(
        text,
        ("Vendor", "Supplier", "Bill From", "From", "Seller", "Company"),
    )
    if vendor:
        return vendor

    lines = [sanitize_text(line) for line in text.splitlines() if line.strip()]
    exclusion = (
        "invoice",
        "bill to",
        "po",
        "date",
        "total",
        "amount",
        "tax",
        "customer",
        "ship to",
    )
    for line in lines[:12]:
        if not any(token in line.lower() for token in exclusion):
            return line
    return None


def parse_invoice_date(text: str) -> Optional[str]:
    labeled_date = find_first_labeled_value(
        text,
        ("Invoice Date", "Date", "Billing Date", "Bill Date"),
    )
    date_candidates: List[str] = []
    if labeled_date:
        date_candidates.append(labeled_date)

    generic_pattern = re.compile(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\b"
    )
    generic_matches = generic_pattern.findall(text)
    date_candidates.extend(generic_matches[:3])

    for candidate in date_candidates:
        parsed = pd.to_datetime(candidate, errors="coerce", dayfirst=False)
        if pd.notna(parsed):
            return parsed.date().isoformat()
    return None


def parse_decimal(value: str) -> Optional[Decimal]:
    cleaned = value.strip()
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned.count(".") > 1:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def extract_amount_by_labels(text: str, labels: Iterable[str]) -> Optional[Decimal]:
    escaped_labels = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"(?im)^\s*(?:{escaped_labels})\b\s*[:\-]?\s*([^\n]+?)\s*$",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    for raw_match in reversed(matches):
        amount_match = re.search(r"[-+]?(?:[$€£₹]\s*)?\d[\d,]*(?:\.\d{1,2})?", raw_match)
        if not amount_match:
            continue
        amount = parse_decimal(amount_match.group(0))
        if amount is not None:
            return amount
    return None


def parse_amounts(text: str) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    subtotal = extract_amount_by_labels(
        text,
        ("Subtotal", "Sub Total", "Taxable Amount", "Net Amount"),
    )
    tax = extract_amount_by_labels(
        text,
        ("Tax", "Tax Amount", "VAT", "GST", "Sales Tax"),
    )
    total = extract_amount_by_labels(
        text,
        ("Grand Total", "Total Due", "Invoice Total", "Amount Due", "Total"),
    )
    return subtotal, tax, total


def validate_record(record: InvoiceRecord) -> List[Tuple[str, str]]:
    issues: List[Tuple[str, str]] = []

    required_fields = {
        "vendor_name": record.vendor_name,
        "invoice_date": record.invoice_date,
        "subtotal": record.subtotal,
        "tax": record.tax,
        "total": record.total,
    }
    for field_name, value in required_fields.items():
        if value in (None, ""):
            issues.append(("missing_field", f"Missing required field: {field_name}"))

    if record.subtotal is not None and record.tax is not None and record.total is not None:
        expected_total = record.subtotal + record.tax
        if abs(expected_total - record.total) > Decimal("0.05"):
            issues.append(
                (
                    "mismatched_total",
                    (
                        "Subtotal + Tax does not match Total "
                        f"({record.subtotal} + {record.tax} != {record.total})"
                    ),
                )
            )

    return issues


def decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def ensure_parent_dir(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)


def process_invoice(pdf_path: Path) -> Tuple[InvoiceRecord, List[InvoiceIssue]]:
    issues: List[InvoiceIssue] = []
    try:
        text = extract_text_from_pdf(pdf_path)
    except Exception as extraction_error:
        detail = f"PDF read failed: {extraction_error}"
        issues.append(
            InvoiceIssue(
                file_name=pdf_path.name,
                file_path=str(pdf_path),
                issue_type="parse_error",
                detail=detail,
            )
        )
        empty_record = InvoiceRecord(
            file_name=pdf_path.name,
            file_path=str(pdf_path),
            vendor_name=None,
            invoice_date=None,
            subtotal=None,
            tax=None,
            total=None,
            anomaly_count=1,
            anomalies=detail,
        )
        return empty_record, issues

    if not text:
        detail = "No extractable text found in PDF."
        issue = InvoiceIssue(
            file_name=pdf_path.name,
            file_path=str(pdf_path),
            issue_type="empty_text",
            detail=detail,
        )
        issues.append(issue)

    vendor_name = parse_vendor_name(text) if text else None
    invoice_date = parse_invoice_date(text) if text else None
    subtotal, tax, total = parse_amounts(text) if text else (None, None, None)

    record = InvoiceRecord(
        file_name=pdf_path.name,
        file_path=str(pdf_path),
        vendor_name=vendor_name,
        invoice_date=invoice_date,
        subtotal=subtotal,
        tax=tax,
        total=total,
        anomaly_count=0,
        anomalies="",
    )

    validation_issues = validate_record(record)
    for issue_type, detail in validation_issues:
        issues.append(
            InvoiceIssue(
                file_name=pdf_path.name,
                file_path=str(pdf_path),
                issue_type=issue_type,
                detail=detail,
            )
        )

    record.anomaly_count = len(issues)
    record.anomalies = " | ".join(issue.detail for issue in issues)
    return record, issues


def write_outputs(
    records: List[InvoiceRecord],
    issues: List[InvoiceIssue],
    output_csv: Path,
    error_report: Path,
) -> None:
    ensure_parent_dir(output_csv)
    ensure_parent_dir(error_report)

    records_df = pd.DataFrame(
        [
            {
                "file_name": record.file_name,
                "file_path": record.file_path,
                "vendor_name": record.vendor_name,
                "invoice_date": record.invoice_date,
                "subtotal": decimal_to_float(record.subtotal),
                "tax": decimal_to_float(record.tax),
                "total": decimal_to_float(record.total),
                "anomaly_count": record.anomaly_count,
                "anomalies": record.anomalies,
            }
            for record in records
        ],
        columns=[
            "file_name",
            "file_path",
            "vendor_name",
            "invoice_date",
            "subtotal",
            "tax",
            "total",
            "anomaly_count",
            "anomalies",
        ],
    )
    records_df.to_csv(output_csv, index=False)

    issues_df = pd.DataFrame(
        [
            {
                "file_name": issue.file_name,
                "file_path": issue.file_path,
                "issue_type": issue.issue_type,
                "detail": issue.detail,
            }
            for issue in issues
        ],
        columns=["file_name", "file_path", "issue_type", "detail"],
    )
    issues_df.to_csv(error_report, index=False)


def main() -> None:
    args = parse_args()
    configure_logging(args.log_file)

    input_folder = Path(args.input_folder)
    output_csv = Path(args.output_csv)
    error_report = Path(args.error_report)

    if not input_folder.exists() or not input_folder.is_dir():
        raise FileNotFoundError(f"Input folder not found or not a directory: {input_folder}")

    pdf_files = list_pdf_files(input_folder, recursive=args.recursive)
    if not pdf_files:
        LOGGER.warning("No PDF files found in folder: %s", input_folder)
        write_outputs([], [], output_csv, error_report)
        LOGGER.info("Wrote empty outputs: %s and %s", output_csv, error_report)
        return

    LOGGER.info("Found %d PDF file(s). Starting extraction.", len(pdf_files))

    records: List[InvoiceRecord] = []
    all_issues: List[InvoiceIssue] = []
    for pdf_file in pdf_files:
        LOGGER.info("Processing: %s", pdf_file.name)
        record, issues = process_invoice(pdf_file)
        records.append(record)
        all_issues.extend(issues)

    write_outputs(records, all_issues, output_csv, error_report)

    valid_count = sum(1 for record in records if record.anomaly_count == 0)
    anomalous_count = len(records) - valid_count
    LOGGER.info("Completed. Valid invoices: %d | Invoices with anomalies: %d", valid_count, anomalous_count)
    LOGGER.info("Cleaned CSV: %s", output_csv)
    LOGGER.info("Error report: %s", error_report)


if __name__ == "__main__":
    main()

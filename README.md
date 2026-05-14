# Invoice PDF Extractor

Extracts key invoice fields from PDF files and generates:

1. A cleaned invoice dataset (`cleaned_invoices.csv`)
2. An anomaly/error report (`invoice_error_report.csv`)
3. A run log (`invoice_extractor.log`)

The script is designed for batch processing and flags records with missing fields or amount mismatches.

## What it extracts

For each invoice PDF, the script attempts to extract:

- `vendor_name`
- `invoice_date` (normalized to `YYYY-MM-DD`)
- `subtotal`
- `tax`
- `total`

It also adds:

- `anomaly_count`
- `anomalies` (human-readable issue summary)

## Validation rules

Each processed invoice is validated. The following issues are reported:

- **missing_field**: one or more required fields are missing (`vendor_name`, `invoice_date`, `subtotal`, `tax`, `total`)
- **mismatched_total**: `subtotal + tax` differs from `total` by more than `0.05`
- **parse_error**: PDF could not be read/extracted
- **empty_text**: PDF had no extractable text

## Extraction approach

1. Text extraction uses `pdfplumber` first.
2. If `pdfplumber` fails, it falls back to `PyPDF2`.
3. Field parsing uses label-based matching with heuristics:
   - Vendor labels: `Vendor`, `Supplier`, `Bill From`, `From`, `Seller`, `Company`
   - Date labels: `Invoice Date`, `Date`, `Billing Date`, `Bill Date`
   - Amount labels:
     - Subtotal: `Subtotal`, `Sub Total`, `Taxable Amount`, `Net Amount`
     - Tax: `Tax`, `Tax Amount`, `VAT`, `GST`, `Sales Tax`
     - Total: `Grand Total`, `Total Due`, `Invoice Total`, `Amount Due`, `Total`
4. Date parsing uses pandas datetime conversion.
5. Numeric parsing strips currency symbols/commas before Decimal conversion.

## Requirements

- Python 3.9+
- Dependencies in `requirements.txt`:
  - `pandas>=2.0.0`
  - `pdfplumber>=0.11.0`
  - `PyPDF2>=3.0.0`

## Setup

From the `Invoice.py` folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

Basic run:

```powershell
python invoice_extractor.py --input-folder "C:\path\to\invoice\folder"
```

Recursive scan:

```powershell
python invoice_extractor.py --input-folder "C:\path\to\invoice\folder" --recursive
```

Custom output locations:

```powershell
python invoice_extractor.py `
  --input-folder "C:\path\to\invoice\folder" `
  --output-csv "C:\path\to\out\cleaned_invoices.csv" `
  --error-report "C:\path\to\out\invoice_error_report.csv" `
  --log-file "C:\path\to\out\invoice_extractor.log"
```

### CLI options

| Option | Short | Required | Default | Description |
|---|---|---|---|---|
| `--input-folder` | `-i` | Yes | N/A | Folder containing invoice PDFs |
| `--output-csv` | `-o` | No | `cleaned_invoices.csv` | Cleaned output CSV path |
| `--error-report` | `-e` | No | `invoice_error_report.csv` | Anomaly/error CSV path |
| `--log-file` | `-l` | No | `invoice_extractor.log` | Log file path |
| `--recursive` | N/A | No | Off | Include PDFs in subfolders |

## Output files

### 1) Cleaned dataset (`cleaned_invoices.csv`)

Columns:

- `file_name`
- `file_path`
- `vendor_name`
- `invoice_date`
- `subtotal`
- `tax`
- `total`
- `anomaly_count`
- `anomalies`

### 2) Error report (`invoice_error_report.csv`)

Columns:

- `file_name`
- `file_path`
- `issue_type`
- `detail`

### 3) Log file (`invoice_extractor.log`)

Includes processing progress, fallback warnings, and completion summary.

## Behavior notes

- If no PDFs are found, the script writes empty output CSVs and logs a warning.
- The script raises an error if `--input-folder` does not exist or is not a directory.
- Parent directories for output files are created automatically.

## Project structure

```text
Invoice.py/
|-- invoice_extractor.py
|-- requirements.txt
|-- README.md
|-- cleaned_invoices.csv
|-- invoice_error_report.csv
|-- invoice_extractor.log
```

## Troubleshooting

- **No extracted data from scanned invoices**: OCR is not included; text must be machine-readable in the PDF.
- **Unexpected vendor/date values**: invoice layouts vary; adjust label sets or parsing heuristics in `invoice_extractor.py`.
- **Missing Python packages**: activate the correct virtual environment and reinstall dependencies.


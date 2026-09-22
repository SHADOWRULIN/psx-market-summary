# PSX Market Summary Automated Pipeline

An automated, serverless ETL pipeline engineered to capture, validate, and archive daily trading summaries from the Pakistan Stock Exchange (PSX) at market close.

---

## Architecture & Logic Flow


```

[External Precision Scheduler]
│
▼ (POST /dispatches)
[GitHub Actions Runner]
│
├─► 1. Pre-Flight Check: Inspect trading calendar (Skip weekends/holidays)
├─► 2. Ingestion: Download daily closing ZIP from PSX DPS portal
├─► 3. Sanitization: Validate ZIP archive structure & guard against traversal
├─► 4. Storage & Processing: Extract files into data staging directories
└─► 5. Delta Commit: Inspect git diff; commit and push only on fresh delta

```

---

## Key Technical Decisions

* **Decoupled Orchestration:** GitHub's native cron scheduler experiences unpredictable queuing delays and inactivity throttles on low-frequency repositories. To achieve exact-minute precision during the PSX market close window (3:25 PM – 4:00 PM PKT), pipeline triggers are decoupled via an authenticated external webhook calling `workflow_dispatch`.
* **Idempotent Ingestion:** The pipeline safely runs across multiple polling intervals without creating duplicate records or committing empty changes (`git diff --staged --quiet`).
* **Fault-Tolerant Network Logic:** Handles intermittent portal latency and ensures corrupted or incomplete market archives are dropped before committing.

---

## Repository Structure


```

├── .github/workflows/
│   └── psx.yml            # CI/CD workflow runner configuration
├── data/                  # Ingested daily raw archives (.lis / market reports)
├── processed/             # Cleaned and extracted daily summaries
├── config.py              # Directory paths, base URLs, and target formats
├── downloader.py          # Primary ETL ingestion and extraction logic
├── requirements.txt       # Environment dependencies
└── LICENSE                # Proprietary license (All Rights Reserved)

```

---

## Local Setup & Manual Execution

### Prerequisites
* Python 3.11+
* Git

### Installation
```bash
# Clone the repository
git clone [https://github.com/SHADOWRULIN/psx-market-summary.git](https://github.com/SHADOWRULIN/psx-market-summary.git)
cd psx-market-summary

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

```

### Running Ingestion

```bash
python downloader.py

```

---

## License

Copyright © 2026 Muhammad Fahaz Khan. All Rights Reserved.

Proprietary and confidential. Unauthorized copying, distribution, or commercial use is strictly prohibited.

# Razorpay AI Finance Controller

An AI-powered finance operations system that automatically reconciles Razorpay payment transactions against settlement records, identifies exceptions, and provides AI-assisted investigation of reconciliation issues.

## Problem

Finance teams often need to manually compare payment records with settlement records to identify:

- Missing settlements
- Amount mismatches
- Orphan settlements
- Settlement variances
- Transactions requiring investigation

This project automates that reconciliation workflow and presents the results through a dashboard.

## Key Features

- Automated payment-to-settlement reconciliation
- Batch processing of 50+ transactions
- Match rate calculation
- Exception detection and classification
- Severity-based exception prioritization
- Financial variance analysis
- Exception drill-down
- AI-assisted investigation of individual exceptions
- AI Finance Controller for natural-language questions
- Razorpay webhook integration
- MySQL database using SQLAlchemy
- FastAPI backend
- Dashboard-based frontend

## Reconciliation Logic

For each captured payment, the system calculates the expected settlement amount:

`Expected Settlement = Gross Amount - Refunds - Fees`

It then compares the expected amount with the corresponding settlement record.

The system identifies:

- `MISSING_SETTLEMENT`
- `AMOUNT_MISMATCH`
- `ORPHAN_SETTLEMENT`

## Sample Reconciliation Results

The current synthetic test batch contains:

| Metric | Result |
|---|---:|
| Total Transactions | 53 |
| Matched Transactions | 47 |
| Exceptions | 7 |
| Match Rate | 88.68% |
| Expected Settlement | ₹6,463.06 |
| Actual Settlement | ₹6,440.42 |
| Net Difference | -₹22.64 |
| Processing Time | ~232 ms |

### Exception Breakdown

- 1 Missing Settlement
- 5 Amount Mismatches
- 1 Orphan Settlement

The exceptions are intentionally included in the synthetic dataset to demonstrate the reconciliation and investigation workflow.

## AI Finance Controller

The system includes an AI layer that can:

- Summarize reconciliation results
- Answer questions about the batch
- Identify the largest reconciliation exception
- Prioritize exceptions for investigation
- Investigate a specific exception
- Suggest investigation and validation steps

The AI is constrained to use verified reconciliation data and is instructed not to invent causes or unsupported financial consequences.

## Architecture

```text
Razorpay
    │
    ├── Payments
    ├── Orders
    └── Webhooks
          │
          ▼
     FastAPI Backend
          │
          ├── Razorpay Client
          ├── Reconciliation Engine
          ├── AI Finance Controller
          │
          ▼
      MySQL Database
          │
          ▼
     Web Dashboard
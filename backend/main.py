import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from dotenv import load_dotenv
from razorpay_client import client
import models
from database import engine, Base, SessionLocal
import json
from fastapi import Depends
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware

from database import get_db
from database import engine
from models import Base, Order, Payment, Refund, SettlementRecord
from sqlalchemy import func

from pydantic import BaseModel



load_dotenv()

from groq import Groq

groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)




app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/orders")
def get_orders():
    return client.order.all()


@app.post("/orders")
def create_order():
    data = {
        "amount": 10000,
        "currency": "INR",
        "receipt": "test_receipt_002"
    }

    order = client.order.create(data)
    return order


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    return client.order.fetch(order_id)


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    return client.payment.fetch(payment_id)


@app.post("/payments/{payment_id}/refund")
def create_refund(payment_id: str):
    data = {
        "amount": 3000
    }

    return client.payment.refund(payment_id, data)


@app.get("/settlements")
def get_settlements():
    return client.settlement.all()


@app.get("/settlements/recon")
def get_settlement_recon(year: int, month: int, day: int):
    return client.settlement.report({
        "year": year,
        "month": month,
        "day": day
    })


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    payload = await request.json()

    db = SessionLocal()

    # Always save the raw webhook
    event = models.WebhookEvent(
        event_type=payload.get("event"),
        payload=json.dumps(payload),
        received_at=payload.get("created_at")
    )

    db.add(event)

    event_type = payload.get("event")

    # Payment captured
    if event_type == "payment.captured":
        payment = payload["payload"]["payment"]["entity"]

        existing = db.query(models.Payment).filter(
            models.Payment.id == payment["id"]
        ).first()

        if not existing:
            db.add(models.Payment(
                id=payment["id"],
                order_id=payment["order_id"],
                amount=payment["amount"],
                currency=payment["currency"],
                status=payment["status"],
                method=payment["method"],
                fee=payment.get("fee", 0),
                tax=payment.get("tax", 0),
                captured=payment.get("captured", False),
                created_at=payment["created_at"]
            ))

    # Order paid
    elif event_type == "order.paid":
        order = payload["payload"]["order"]["entity"]

        existing = db.query(models.Order).filter(
            models.Order.id == order["id"]
        ).first()

        if not existing:
            db.add(models.Order(
                id=order["id"],
                amount=order["amount"],
                currency=order["currency"],
                status=order["status"],
                created_at=order["created_at"]
            ))

        # Refund created
    elif event_type == "refund.created":
        refund = payload["payload"]["refund"]["entity"]

        existing = db.query(models.Refund).filter(
            models.Refund.id == refund["id"]
        ).first()

        if not existing:
            db.add(models.Refund(
                id=refund["id"],
                payment_id=refund["payment_id"],
                amount=refund["amount"],
                currency=refund["currency"],
                status=refund["status"],
                created_at=refund["created_at"]
            ))

    # Refund processed
    elif event_type == "refund.processed":
        refund = payload["payload"]["refund"]["entity"]

        existing = db.query(models.Refund).filter(
            models.Refund.id == refund["id"]
        ).first()

        if existing:
            existing.status = refund["status"]
        else:
            db.add(models.Refund(
                id=refund["id"],
                payment_id=refund["payment_id"],
                amount=refund["amount"],
                currency=refund["currency"],
                status=refund["status"],
                created_at=refund["created_at"]
            ))

    # Refund failed
    elif event_type == "refund.failed":
        refund = payload["payload"]["refund"]["entity"]

        existing = db.query(models.Refund).filter(
            models.Refund.id == refund["id"]
        ).first()

        if existing:
            existing.status = refund["status"]

    db.commit()
    db.close()

    print("Processed:", event_type)

    return {"status": "received"}

@app.get("/")
def checkout():
    key_id = os.getenv("RAZORPAY_KEY_ID")

    html = open("static/index.html").read()
    html = html.replace("YOUR_TEST_KEY_ID", key_id)

    return HTMLResponse(content=html)

@app.get("/refunds/{refund_id}")
def get_refund(refund_id: str):
    return client.refund.fetch(refund_id)

@app.get("/reconciliation/payment/{payment_id}")
def reconcile_payment(payment_id: str, db: Session = Depends(get_db)):

    # 1. Get payment from our database
    payment = db.query(Payment).filter(
        Payment.id == payment_id
    ).first()

    if not payment:
        return {
            "status": "error",
            "message": "Payment not found"
        }

    # 2. Get processed refunds for this payment
    refunds = db.query(Refund).filter(
        Refund.payment_id == payment_id,
        Refund.status == "processed"
    ).all()

    # 3. Calculate total refunded amount
    total_refunded = sum(
        refund.amount or 0
        for refund in refunds
    )

    # 4. Calculate expected settlement amount
    gross_amount = payment.amount or 0
    fee = payment.fee or 0

    expected_net = gross_amount - total_refunded - fee

    return {
        "payment_id": payment.id,
        "order_id": payment.order_id,

        "gross_amount_paise": gross_amount,
        "refunded_amount_paise": total_refunded,
        "fee_paise": fee,

        "expected_net_paise": expected_net,

        "gross_amount_rupees": gross_amount / 100,
        "refunded_amount_rupees": total_refunded / 100,
        "fee_rupees": fee / 100,
        "expected_net_rupees": expected_net / 100,

        "payment_status": payment.status,
        "reconciliation_status": "reconciled"
    }

@app.get("/reconciliation/batch")
def reconcile_batch(db: Session = Depends(get_db)):

    payments = db.query(Payment).all()

    results = []

    total_gross = 0
    total_refunded = 0
    total_fees = 0
    total_expected_net = 0

    reconciled_count = 0
    exception_count = 0

    for payment in payments:

        refunds = db.query(Refund).filter(
            Refund.payment_id == payment.id,
            Refund.status == "processed"
        ).all()

        refunded_amount = sum(
            refund.amount or 0
            for refund in refunds
        )

        gross_amount = payment.amount or 0
        fee = payment.fee or 0

        expected_net = gross_amount - refunded_amount - fee

        if payment.status == "captured":
            reconciliation_status = "reconciled"
            reconciled_count += 1
        else:
            reconciliation_status = "exception"
            exception_count += 1

        total_gross += gross_amount
        total_refunded += refunded_amount
        total_fees += fee
        total_expected_net += expected_net

        results.append({
            "payment_id": payment.id,
            "order_id": payment.order_id,
            "gross_amount_paise": gross_amount,
            "refunded_amount_paise": refunded_amount,
            "fee_paise": fee,
            "expected_net_paise": expected_net,
            "payment_status": payment.status,
            "reconciliation_status": reconciliation_status
        })

    return {
        "summary": {
            "total_transactions": len(payments),
            "reconciled": reconciled_count,
            "exceptions": exception_count,

            "total_gross_paise": total_gross,
            "total_refunded_paise": total_refunded,
            "total_fees_paise": total_fees,
            "total_expected_net_paise": total_expected_net,

            "total_gross_rupees": total_gross / 100,
            "total_refunded_rupees": total_refunded / 100,
            "total_fees_rupees": total_fees / 100,
            "total_expected_net_rupees": total_expected_net / 100
        },

        "transactions": results
    }

@app.post("/test/generate-data")
def generate_test_data(db: Session = Depends(get_db)):

    import random
    import time

    # Find current number of test orders
    existing_orders = db.query(Order).count()

    created = 0

    for i in range(50):

        order_id = f"test_order_{existing_orders + i + 1}"
        payment_id = f"test_pay_{existing_orders + i + 1}"

        amount = random.choice([
            5000,      # ₹50
            10000,     # ₹100
            15000,     # ₹150
            20000,     # ₹200
            25000      # ₹250
        ])

        fee = round(amount * 0.0236)

        order = Order(
            id=order_id,
            amount=amount,
            currency="INR",
            status="paid",
            created_at=int(time.time())
        )

        payment = Payment(
            id=payment_id,
            order_id=order_id,
            amount=amount,
            currency="INR",
            status="captured",
            method="netbanking",
            fee=fee,
            tax=0,
            captured=True,
            created_at=int(time.time())
        )

        db.add(order)
        db.add(payment)
        created += 1

    db.commit()

    return {
        "status": "success",
        "records_created": created
    }

@app.post("/test/generate-settlements")
def generate_test_settlements(db: Session = Depends(get_db)):

    import random
    import time

    payments = db.query(Payment).all()

    created = 0
    exceptions_created = 0

    for payment in payments:

        # Skip if settlement already exists
        existing = db.query(SettlementRecord).filter(
            SettlementRecord.payment_id == payment.id
        ).first()

        if existing:
            continue

        # Calculate expected amount
        refunds = db.query(Refund).filter(
            Refund.payment_id == payment.id,
            Refund.status == "processed"
        ).all()

        total_refunded = sum(
            refund.amount or 0
            for refund in refunds
        )

        expected_amount = (
            (payment.amount or 0)
            - total_refunded
            - (payment.fee or 0)
        )

        # Normally settlement equals expected amount
        settled_amount = expected_amount

        # Create a few deliberate exceptions
        if payment.id == "test_pay_10":
            settled_amount = expected_amount - 100
            exceptions_created += 1

        elif payment.id == "test_pay_20":
            settled_amount = expected_amount + 200
            exceptions_created += 1

        elif payment.id == "test_pay_30":
            settled_amount = expected_amount - 150
            exceptions_created += 1

        elif payment.id == "test_pay_40":
            settled_amount = expected_amount + 300
            exceptions_created += 1

        elif payment.id == "test_pay_50":
            settled_amount = expected_amount - 250
            exceptions_created += 1

        settlement = SettlementRecord(
            payment_id=payment.id,
            settlement_id=f"settlement_{payment.id}",
            settled_amount=settled_amount,
            status="settled",
            created_at=int(time.time())
        )

        db.add(settlement)
        created += 1

    db.commit()

    return {
        "status": "success",
        "settlement_records_created": created,
        "intentional_exceptions": exceptions_created
    }

# ============================================================
# RECONCILIATION ENGINE
# ============================================================

def run_reconciliation(db: Session):

    payments = db.query(Payment).all()
    settlements = db.query(SettlementRecord).all()

    settlement_map = {
        settlement.payment_id: settlement
        for settlement in settlements
    }

    payment_ids = set(payment.id for payment in payments)

    transactions = []
    exceptions = []

    total_expected = 0
    total_settled = 0
    matched = 0

    # --------------------------------------------------------
    # PAYMENT → SETTLEMENT RECONCILIATION
    # --------------------------------------------------------

    for payment in payments:

        gross_amount = payment.amount or 0

        refunds = db.query(Refund).filter(
            Refund.payment_id == payment.id,
            Refund.status == "processed"
        ).all()

        total_refunded = sum(
            refund.amount or 0
            for refund in refunds
        )

        fee = payment.fee or 0

        expected_amount = (
            gross_amount
            - total_refunded
            - fee
        )

        total_expected += expected_amount

        # PAYMENT NOT CAPTURED
        if not payment.captured:

            difference = -expected_amount

            exception = {
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "type": "PAYMENT_NOT_CAPTURED",
                "severity": "HIGH",
                "gross_amount_rupees": gross_amount / 100,
                "refunded_amount_rupees": total_refunded / 100,
                "fee_rupees": fee / 100,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": 0,
                "difference_rupees": difference / 100,
                "reason": "Payment exists but was not captured"
            }

            exceptions.append(exception)

            transactions.append({
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": 0,
                "difference_rupees": difference / 100,
                "status": "exception",
                "exception_type": "PAYMENT_NOT_CAPTURED"
            })

            continue

        # MISSING SETTLEMENT
        settlement = settlement_map.get(payment.id)

        if not settlement:

            difference = -expected_amount

            exception = {
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "type": "MISSING_SETTLEMENT",
                "severity": "HIGH",
                "gross_amount_rupees": gross_amount / 100,
                "refunded_amount_rupees": total_refunded / 100,
                "fee_rupees": fee / 100,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": 0,
                "difference_rupees": difference / 100,
                "reason": (
                    "Captured payment has no corresponding "
                    "settlement record"
                )
            }

            exceptions.append(exception)

            transactions.append({
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": 0,
                "difference_rupees": difference / 100,
                "status": "exception",
                "exception_type": "MISSING_SETTLEMENT"
            })

            continue

        # MATCH / AMOUNT MISMATCH
        actual_amount = settlement.settled_amount or 0

        total_settled += actual_amount

        difference = actual_amount - expected_amount

        if difference == 0:

            matched += 1

            transactions.append({
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": actual_amount / 100,
                "difference_rupees": 0,
                "status": "matched",
                "exception_type": None
            })

        else:

            severity = "MEDIUM"

            if abs(difference) > 500:
                severity = "HIGH"

            exception = {
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "type": "AMOUNT_MISMATCH",
                "severity": severity,
                "gross_amount_rupees": gross_amount / 100,
                "refunded_amount_rupees": total_refunded / 100,
                "fee_rupees": fee / 100,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": actual_amount / 100,
                "difference_rupees": difference / 100,
                "reason": (
                    "Settlement amount is higher than expected"
                    if difference > 0
                    else "Settlement amount is lower than expected"
                )
            }

            exceptions.append(exception)

            transactions.append({
                "payment_id": payment.id,
                "order_id": payment.order_id,
                "expected_amount_rupees": expected_amount / 100,
                "actual_amount_rupees": actual_amount / 100,
                "difference_rupees": difference / 100,
                "status": "exception",
                "exception_type": "AMOUNT_MISMATCH"
            })

    # --------------------------------------------------------
    # SETTLEMENT → PAYMENT RECONCILIATION
    # --------------------------------------------------------

    for settlement in settlements:

        if settlement.payment_id not in payment_ids:

            actual_amount = settlement.settled_amount or 0

            total_settled += actual_amount

            exceptions.append({
                "payment_id": settlement.payment_id,
                "order_id": None,
                "type": "ORPHAN_SETTLEMENT",
                "severity": "HIGH",
                "gross_amount_rupees": 0,
                "refunded_amount_rupees": 0,
                "fee_rupees": 0,
                "expected_amount_rupees": 0,
                "actual_amount_rupees": actual_amount / 100,
                "difference_rupees": actual_amount / 100,
                "reason": (
                    "Settlement exists without a "
                    "corresponding payment"
                )
            })

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    total_transactions = len(payments)

    exception_count = len(exceptions)

    match_rate = (
        (matched / total_transactions) * 100
        if total_transactions > 0
        else 0
    )

    net_difference = total_settled - total_expected

    summary = {
        "total_transactions": total_transactions,
        "matched": matched,
        "exceptions": exception_count,
        "match_rate_percent": round(match_rate, 2),
        "expected_settlement_rupees": round(
            total_expected / 100, 2
        ),
        "actual_settlement_rupees": round(
            total_settled / 100, 2
        ),
        "difference_rupees": round(
            net_difference / 100, 2
        )
    }

    # --------------------------------------------------------
    # VERIFIED EXCEPTION IMPACT
    # --------------------------------------------------------

    high_count = 0
    medium_count = 0
    low_count = 0

    total_absolute_variance = 0
    positive_variance = 0
    negative_variance = 0

    exception_counts = {}

    largest_exception = None

    for exception in exceptions:

        exception_type = exception["type"]

        exception_counts[exception_type] = (
            exception_counts.get(exception_type, 0) + 1
        )

        severity = exception["severity"]

        if severity == "HIGH":
            high_count += 1

        elif severity == "MEDIUM":
            medium_count += 1

        elif severity == "LOW":
            low_count += 1

        difference = exception.get(
            "difference_rupees", 0
        )

        total_absolute_variance += abs(difference)

        if difference > 0:
            positive_variance += difference

        elif difference < 0:
            negative_variance += abs(difference)

        if (
            largest_exception is None
            or abs(difference)
            > abs(
                largest_exception["difference_rupees"]
            )
        ):
            largest_exception = exception

    exception_impact = {
        "total_exceptions": exception_count,

        "high_severity_count": high_count,

        "medium_severity_count": medium_count,

        "low_severity_count": low_count,

        "total_absolute_variance_rupees": round(
            total_absolute_variance,
            2
        ),

        "positive_variance_rupees": round(
            positive_variance,
            2
        ),

        "negative_variance_rupees": round(
            negative_variance,
            2
        ),

        "exception_breakdown": exception_counts,

        "largest_exception": (
            {
                "type": largest_exception["type"],
                "payment_id": largest_exception["payment_id"],
                "severity": largest_exception["severity"],
                "difference_rupees":
                    largest_exception["difference_rupees"]
            }
            if largest_exception
            else None
        )
    }

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return {
        "summary": summary,
        "transactions": transactions,
        "exceptions": exceptions,
        "exception_impact": exception_impact
    }
# ============================================================
# RECONCILIATION ENDPOINT
# ============================================================
@app.post("/reconciliation/run")
def run_reconciliation_batch(
    db: Session = Depends(get_db)
):
    import time

    start_time = time.time()

    # Run the verified reconciliation engine
    result = run_reconciliation(db)

    end_time = time.time()

    processing_time_ms = round(
        (end_time - start_time) * 1000,
        2
    )

    summary = result["summary"]
    exception_impact = result["exception_impact"]

    return {
        "status": "completed",

        "run_timestamp": int(end_time),

        "processing_time_ms": processing_time_ms,

        "batch_report": {
            "records_processed":
                summary["total_transactions"],

            "records_matched":
                summary["matched"],

            "unresolved_exceptions":
                summary["exceptions"],

            "match_rate_percent":
                summary["match_rate_percent"],

            "expected_settlement_rupees":
                summary["expected_settlement_rupees"],

            "actual_settlement_rupees":
                summary["actual_settlement_rupees"],

            "net_difference_rupees":
                summary["difference_rupees"]
        },

        "exception_summary": {
            "high":
                exception_impact[
                    "high_severity_count"
                ],

            "medium":
                exception_impact[
                    "medium_severity_count"
                ],

            "low":
                exception_impact[
                    "low_severity_count"
                ],

            "breakdown":
                exception_impact[
                    "exception_breakdown"
                ]
        }
    } 


@app.get("/reconciliation/settlements")
def reconcile_settlements(
    db: Session = Depends(get_db)
):

    return run_reconciliation(db)



# ============================================================
# AI SUMMARY
# ============================================================

@app.get("/ai/summary")
def ai_summary(
    db: Session = Depends(get_db)
):

    reconciliation = run_reconciliation(db)

    summary = reconciliation["summary"]

    exceptions = reconciliation["exceptions"]

    exception_types = list(
        set(
            item["type"]
            for item in exceptions
        )
    )

    highest_priority_exception = (
        exceptions[0]
        if exceptions
        else None
    )

    total_exception_difference = round(
        sum(
            item["difference_rupees"]
            for item in exceptions
        ),
        2
    )

    return {

        "summary": {

            "total_transactions":
                summary["total_transactions"],

            "matched":
                summary["matched"],

            "exceptions":
                summary["exceptions"],

            "match_rate_percent":
                summary["match_rate_percent"],

            "expected_settlement_rupees":
                summary["expected_settlement_rupees"],

            "actual_settlement_rupees":
                summary["actual_settlement_rupees"],

            "difference_rupees":
                summary["difference_rupees"]
        },

        "ai_context": {

            "exception_count":
                len(exceptions),

            "exception_types":
                exception_types,

            "highest_priority_exception":
                highest_priority_exception,

            "total_exception_difference_rupees":
                total_exception_difference
        },

        "exceptions":
            exceptions
    }

# ============================================================
# AI QUESTION MODEL
# ============================================================

class AIQuestion(BaseModel):

    question: str



# ============================================================
# AI FINANCE CONTROLLER
# ============================================================


@app.post("/ai/ask")
def ai_ask(
    request: AIQuestion,
    db: Session = Depends(get_db)
):

    question = request.question.strip()

    if not question:
        return {
            "question": question,
            "answer": "Please enter a question."
        }

    # Run the same verified reconciliation engine
    reconciliation = run_reconciliation(db)

    summary = reconciliation["summary"]
    exceptions = reconciliation["exceptions"]
    exception_impact = reconciliation["exception_impact"]

    q = question.lower()

        # ========================================================
    # SPECIFIC EXCEPTION INVESTIGATION
    # ========================================================

    if "investigate this specific reconciliation exception" in q:

        current_exception = None

        # Find the exact exception using its payment ID
        for exception in exceptions:

            payment_id = str(
                exception.get("payment_id", "")
            ).lower()

            if payment_id and payment_id in q:
                current_exception = exception
                break

        if not current_exception:

            return {
                "question": question,
                "answer": (
                    "The specific reconciliation exception "
                    "could not be identified from the verified data."
                )
            }

        investigation_context = {
            "type":
                current_exception["type"],

            "severity":
                current_exception["severity"],

            "payment_id":
                current_exception["payment_id"],

            "order_id":
                current_exception.get(
                    "order_id"
                ),

            "expected_amount_rupees":
                current_exception[
                    "expected_amount_rupees"
                ],

            "actual_amount_rupees":
                current_exception[
                    "actual_amount_rupees"
                ],

            "difference_rupees":
                current_exception[
                    "difference_rupees"
                ],

            "reason":
                current_exception.get(
                    "reason",
                    ""
                )
        }

        investigation_prompt = f"""
You are an AI Finance Controller investigating ONE
specific reconciliation exception.

Use ONLY the verified exception data below.

VERIFIED EXCEPTION DATA:

{investigation_context}

STRICT RULES:

1. Do not invent facts.

2. Do not assume the root cause.

3. Do not claim fraud, financial loss, revenue leakage,
accounting error, compliance violation, duplicate payment,
missing money, or system failure unless explicitly proven
by the verified data.

4. The verified reason describes what the reconciliation
engine observed. It does NOT necessarily establish the
underlying root cause.

5. If the cause is unknown, explicitly state:

"The available reconciliation data does not establish
the cause."

6. Do not recommend modifying, deleting, reversing, refunding,
or creating financial records.

7. Recommendations must only be investigation or validation
steps.

8. Use the exact verified numbers.

9. Currency is Indian Rupees (₹).

Respond using exactly this structure:

Finding:
State what the reconciliation data verifies.

Risk:
State only what can safely be concluded from the exception.
If the business impact is not established, say so.

Recommended investigation steps:
Give 2-4 practical investigation or validation steps.

Do not invent a root cause.
"""

        response = groq_client.chat.completions.create(

            model="openai/gpt-oss-120b",

            messages=[

                {
                    "role": "system",
                    "content": (
                        "You are a highly reliable AI Finance "
                        "Controller. Investigate only the verified "
                        "exception provided. Never invent causes "
                        "or financial consequences."
                    )
                },

                {
                    "role": "user",
                    "content": investigation_prompt
                }

            ]
        )

        return {
            "question": question,
            "answer": (
                response
                .choices[0]
                .message
                .content
            )
        }

    # ========================================================
    # DETERMINISTIC FINANCIAL QUESTIONS
    # ========================================================

    # MATCH RATE
    if "match rate" in q:

        return {
            "question": question,
            "answer": (
                f"The current reconciliation match rate is "
                f"{summary['match_rate_percent']}%."
            )
        }

    # TOTAL TRANSACTIONS
    if (
        "total transaction" in q
        or "number of transaction" in q
    ):

        return {
            "question": question,
            "answer": (
                f"There are "
                f"{summary['total_transactions']} "
                f"transactions in the current reconciliation."
            )
        }

    # MATCHED TRANSACTIONS
    if (
        "how many matched" in q
        or "matched transaction" in q
    ):

        return {
            "question": question,
            "answer": (
                f"{summary['matched']} transactions are "
                f"currently matched."
            )
        }

    # EXCEPTIONS
    if (
        "how many exception" in q
        or "number of exception" in q
    ):

        return {
            "question": question,
            "answer": (
                f"There are "
                f"{summary['exceptions']} "
                f"reconciliation exceptions."
            )
        }

    # EXPECTED SETTLEMENT
    if "expected settlement" in q:

        return {
            "question": question,
            "answer": (
                f"The expected settlement amount is "
                f"₹{summary['expected_settlement_rupees']:.2f}."
            )
        }

    # ACTUAL SETTLEMENT
    if "actual settlement" in q:

        return {
            "question": question,
            "answer": (
                f"The actual settlement amount is "
                f"₹{summary['actual_settlement_rupees']:.2f}."
            )
        }

    # ========================================================
    # NET DIFFERENCE
    # ========================================================

    if (
        "difference" in q
        or "net difference" in q
    ):

        difference = summary["difference_rupees"]

        if difference < 0:

            formatted_difference = (
                f"-₹{abs(difference):.2f}"
            )

        else:

            formatted_difference = (
                f"₹{difference:.2f}"
            )

        return {
            "question": question,
            "answer": (
                f"The net settlement difference is "
                f"{formatted_difference}."
            )
        }

    # ========================================================
    # EXCEPTION BREAKDOWN
    # ========================================================

    if (
        "exception type" in q
        or "types of exception" in q
        or "breakdown of exception" in q
        or "exception breakdown" in q
    ):

        breakdown = []

        for exception_type, count in (
            exception_impact[
                "exception_breakdown"
            ].items()
        ):

            breakdown.append(
                f"{exception_type}: {count}"
            )

        if not breakdown:

            answer = (
                "There are currently no "
                "reconciliation exceptions."
            )

        else:

            answer = (
                "Current exception breakdown:\n\n"
                + "\n".join(breakdown)
            )

        return {
            "question": question,
            "answer": answer
        }

    # ========================================================
    # LARGEST MISMATCH
    # ========================================================

    if (
        "largest mismatch" in q
        or "biggest mismatch" in q
    ):

        largest = (
            exception_impact[
                "largest_exception"
            ]
        )

        if not largest:

            return {
                "question": question,
                "answer": (
                    "There are no reconciliation "
                    "exceptions."
                )
            }

        difference = largest[
            "difference_rupees"
        ]

        if difference < 0:

            formatted_difference = (
                f"-₹{abs(difference):.2f}"
            )

        else:

            formatted_difference = (
                f"₹{difference:.2f}"
            )

        return {
            "question": question,
            "answer": (
                f"The largest exception is "
                f"{largest['type']}.\n\n"

                f"Payment ID: "
                f"{largest['payment_id']}\n"

                f"Difference: "
                f"{formatted_difference}\n"

                f"Severity: "
                f"{largest['severity']}"
            )
        }

    # ========================================================
    # INVESTIGATION PRIORITY
    # ========================================================

    if (
        "investigate first" in q
        or "investigate" in q
        or "priority" in q
        or "prioritize" in q
        or "what should i look at" in q
    ):

        if not exceptions:

            return {
                "question": question,
                "answer": (
                    "There are currently no "
                    "exceptions to investigate."
                )
            }

        severity_rank = {
            "HIGH": 3,
            "MEDIUM": 2,
            "LOW": 1
        }

        prioritized = sorted(
            exceptions,
            key=lambda x: (
                severity_rank.get(
                    x.get("severity"),
                    0
                ),
                abs(
                    x.get(
                        "difference_rupees",
                        0
                    )
                )
            ),
            reverse=True
        )

        top_exceptions = prioritized[:3]

        lines = [
            "Priority investigation items:\n"
        ]

        for index, exception in enumerate(
            top_exceptions,
            start=1
        ):

            difference = exception[
                "difference_rupees"
            ]

            if difference < 0:

                formatted_difference = (
                    f"-₹{abs(difference):.2f}"
                )

            else:

                formatted_difference = (
                    f"₹{difference:.2f}"
                )

            lines.append(
                f"{index}. "
                f"{exception['type']} — "
                f"Payment "
                f"{exception['payment_id']} — "
                f"Difference "
                f"{formatted_difference} — "
                f"Severity "
                f"{exception['severity']}"
            )

        lines.append(
            "\nStart with the highest-severity "
            "exception, then review the largest "
            "financial discrepancies."
        )

        return {
            "question": question,
            "answer": "\n".join(lines)
        }

    # ========================================================
    # VERIFIED DATA FOR GROQ
    # ========================================================

    financial_context = {

        "total_transactions":
            summary[
                "total_transactions"
            ],

        "matched_transactions":
            summary[
                "matched"
            ],

        "total_exceptions":
            exception_impact[
                "total_exceptions"
            ],

        "match_rate_percent":
            summary[
                "match_rate_percent"
            ],

        "expected_settlement_rupees":
            summary[
                "expected_settlement_rupees"
            ],

        "actual_settlement_rupees":
            summary[
                "actual_settlement_rupees"
            ],

        "net_difference_rupees":
            summary[
                "difference_rupees"
            ],

        "high_severity_count":
            exception_impact[
                "high_severity_count"
            ],

        "medium_severity_count":
            exception_impact[
                "medium_severity_count"
            ],

        "low_severity_count":
            exception_impact[
                "low_severity_count"
            ],

        "total_absolute_variance_rupees":
            exception_impact[
                "total_absolute_variance_rupees"
            ],

        "positive_variance_rupees":
            exception_impact[
                "positive_variance_rupees"
            ],

        "negative_variance_rupees":
            exception_impact[
                "negative_variance_rupees"
            ],

        "exception_breakdown":
            exception_impact[
                "exception_breakdown"
            ],

        "largest_exception":
            exception_impact[
                "largest_exception"
            ]
    }

    # ========================================================
    # VERIFIED EXCEPTION DETAILS
    # ========================================================

    financial_context[
        "verified_exceptions"
    ] = []

    for exception in exceptions:

        financial_context[
            "verified_exceptions"
        ].append({

            "type":
                exception[
                    "type"
                ],

            "severity":
                exception[
                    "severity"
                ],

            "payment_id":
                exception[
                    "payment_id"
                ],

            "expected_amount_rupees":
                exception[
                    "expected_amount_rupees"
                ],

            "actual_amount_rupees":
                exception[
                    "actual_amount_rupees"
                ],

            "difference_rupees":
                exception[
                    "difference_rupees"
                ],

            "reason":
                exception.get(
                    "reason",
                    ""
                )
        })

    # ========================================================
    # GROQ PROMPT
    # ========================================================

    prompt = f"""
You are an AI Finance Controller assisting a finance
operations team with payment reconciliation.

Your ONLY source of financial truth is the VERIFIED DATA
provided below.

VERIFIED DATA:

{financial_context}

========================================================
STRICT FINANCIAL SAFETY RULES
========================================================

1. Use ONLY the verified data provided above.

2. NEVER invent financial facts.

3. NEVER calculate a financial metric yourself when the
required metric is not explicitly provided in the
verified data.

4. NEVER invent or assume the root cause of an exception.

5. NEVER claim:

- fraud
- revenue leakage
- accounting error
- compliance violation
- duplicate payment
- system failure
- incorrect posting
- missing money
- financial loss

unless the verified data explicitly proves it.

6. NEVER assume why an amount mismatch occurred.

Do NOT say it was caused by:

- fees
- taxes
- rounding
- discounts
- currency conversion
- duplicate processing
- data-entry errors

unless that cause is explicitly present in the
verified data.

7. NEVER describe an exception as an actual financial
loss or gain.

A reconciliation variance is a difference between
verified values. It is NOT automatically a financial
loss or gain.

8. NEVER instruct the finance team to:

- create a financial record
- delete a financial record
- reverse a transaction
- modify a settlement
- modify a payment
- issue a refund
- change accounting entries

based only on this reconciliation data.

9. Recommendations must be INVESTIGATION STEPS only.

10. When the cause is unknown, explicitly say:

"The available reconciliation data does not establish
the cause."

11. Clearly distinguish between:

VERIFIED FACT:
What the reconciliation data explicitly shows.

INVESTIGATION STEP:
A safe action the finance team can take to investigate
or validate the exception.

12. Use exact verified numbers.

13. Currency is Indian Rupees (₹).

14. These metrics are different and must NEVER be treated
as interchangeable:

- net_difference_rupees
- total_absolute_variance_rupees
- positive_variance_rupees
- negative_variance_rupees

15. NEVER sum individual exception differences and call
the result:

- financial impact
- cash flow impact
- loss
- gain

unless that exact metric is explicitly provided.

16. Do not claim that the reconciliation process itself
is defective merely because exceptions exist.

17. Do not claim that an exception has caused a business
impact unless the verified data explicitly establishes
that impact.

18. Keep answers concise and suitable for a finance
operations team.

========================================================
ANSWERING STYLE
========================================================

For analytical questions, use:

Finding:
State only verified facts.

Risk:
State only risks directly supported by the reconciliation
data.

If the data does not establish a specific business risk,
say:

"The reconciliation data identifies an exception, but
does not establish its business impact."

Recommended action:
Provide investigation or validation steps only.

Do not tell the finance team to directly alter financial
records based on this analysis.

========================================================
USER QUESTION
========================================================

{question}

Answer the question directly.
"""

    # ========================================================
    # GROQ REQUEST
    # ========================================================

    response = groq_client.chat.completions.create(

        model="openai/gpt-oss-120b",

        messages=[

            {
                "role": "system",
                "content": (
                    "You are a highly reliable AI Finance "
                    "Controller. Financial accuracy is more "
                    "important than speculation. Use only "
                    "verified financial data. Never invent "
                    "facts, causes, financial impact, or "
                    "business consequences."
                )
            },

            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return {
        "question": question,
        "answer": (
            response
            .choices[0]
            .message
            .content
        )
    }


@app.post("/test/generate-extra-exceptions")
def generate_extra_exceptions(db: Session = Depends(get_db)):

    import time

    # -------------------------------
    # MISSING SETTLEMENT
    # -------------------------------

    missing_payment_id = "exception_missing_payment"
    missing_order_id = "exception_missing_order"

    existing_payment = db.query(Payment).filter(
        Payment.id == missing_payment_id
    ).first()

    if not existing_payment:

        payment = Payment(
            id=missing_payment_id,
            order_id=missing_order_id,
            amount=10000,
            currency="INR",
            status="captured",
            method="netbanking",
            fee=236,
            tax=0,
            captured=True,
            created_at=int(time.time())
        )

        db.add(payment)

    # -------------------------------
    # ORPHAN SETTLEMENT
    # -------------------------------

    orphan_payment_id = "exception_orphan_payment"

    existing_settlement = db.query(SettlementRecord).filter(
        SettlementRecord.payment_id == orphan_payment_id
    ).first()

    if not existing_settlement:

        settlement = SettlementRecord(
            payment_id=orphan_payment_id,
            settlement_id="settlement_orphan_001",
            settled_amount=7500,
            status="settled",
            created_at=int(time.time())
        )

        db.add(settlement)

    db.commit()

    return {
        "status": "success",
        "missing_settlement_created": True,
        "orphan_settlement_created": True
    }
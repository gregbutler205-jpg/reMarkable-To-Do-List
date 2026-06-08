"""
Summary email with the rendered page attached as a PDF.
Uses plain smtplib so there are no extra dependencies beyond the stdlib.
"""
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

import config


def send_summary(
    pdf_path: str,
    done: int,
    demoted: int,
    promoted: int,
    new_items: list[dict],
    carried: int,
    uncertain: list[dict],
    provider: str,
    errors: str = "",
    page_image_path: str | None = None,
):
    """
    Send the nightly summary email with the rendered PDF attached.
    No-op (prints to stdout) when SMTP_USER is not configured.
    """
    if not config.SMTP_USER:
        _print_fallback(done, demoted, promoted, new_items, carried, uncertain, errors)
        return

    subject = f"To-Do loop · {done} done · {new_items and len(new_items) or 0} new"
    body = _build_body(done, demoted, promoted, new_items, carried, uncertain, provider, errors)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = config.EMAIL_FROM
    msg["To"]      = config.EMAIL_TO
    msg["Date"]    = formatdate()
    msg.set_content(body)

    if pdf_path:
        with open(pdf_path, "rb") as fh:
            msg.add_attachment(
                fh.read(),
                maintype="application",
                subtype="pdf",
                filename="tomorrow.pdf",
            )

    if page_image_path:
        try:
            with open(page_image_path, "rb") as fh:
                msg.add_attachment(
                    fh.read(),
                    maintype="image",
                    subtype="png",
                    filename="page_read_by_llm.png",
                )
        except Exception:
            pass

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASS)
        smtp.send_message(msg)


def send_alert(subject: str, body: str):
    """Send a failure alert. Falls back to print if SMTP is not configured."""
    if not config.SMTP_USER:
        print(f"ALERT: {subject}\n{body}")
        return

    msg = EmailMessage()
    msg["Subject"] = f"[ALERT] To-Do loop · {subject}"
    msg["From"]    = config.EMAIL_FROM
    msg["To"]      = config.EMAIL_TO
    msg["Date"]    = formatdate()
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASS)
        smtp.send_message(msg)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_body(done, demoted, promoted, new_items, carried, uncertain, provider, errors):
    lines = [
        f"Provider : {provider}",
        f"Done     : {done}",
        f"Promoted : {promoted}  (→ Priorities)",
        f"Demoted  : {demoted}   (→ Someday)",
        f"Carried  : {carried}",
        f"New      : {len(new_items)}",
        "",
    ]

    if new_items:
        lines.append("New items transcribed:")
        for item in new_items:
            conf = item.get("confidence", "?")
            lines.append(f"  [{conf}] {item['text']}")
        lines.append("")

    if uncertain:
        lines.append("Uncertain / ambiguous (no state change):")
        for u in uncertain:
            lines.append(f"  {u.get('about', '?')}: {u.get('note', '')}")
        lines.append("")

    if errors:
        lines.append(f"Errors: {errors}")

    lines.append("The rendered page for tomorrow is attached.")
    return "\n".join(lines)


def _print_fallback(done, demoted, promoted, new_items, carried, uncertain, errors):
    print("=== Nightly summary (SMTP not configured) ===")
    print(_build_body(done, demoted, promoted, new_items, carried, uncertain, "?", errors))

"""Reminder emails sent to employees whose expenses need more detail."""

from flask import render_template_string
from markupsafe import Markup

REMINDER_TEMPLATE = """Hi {{ employee.name.split()[0] }},

Your {{ expense.category|lower }} expense at {{ expense.merchant }} for {{ expense.amount_cents|money }}
on {{ expense.spent_on }} needs more detail before {{ sender.name }} can approve it.

You wrote:

    {{ description }}

Please add an itemised receipt and the business purpose in Darts.

Thanks,
{{ sender.name }}
{{ sender.title }}
"""


def build_reminder(expense, employee, sender) -> tuple[str, str]:
    subject = f"Action needed: your {expense['merchant']} expense"
    # Quote the employee's own words, indented to match the email layout.
    quoted = "\n    ".join((expense["description"] or "(no description)").splitlines())
    # The description is email content, not template source: pass it as a
    # context value so stored Jinja syntax in it is never compiled. Markup
    # keeps the inner render from HTML-escaping it; reminder.html escapes
    # the finished body once when displaying it.
    body = render_template_string(
        REMINDER_TEMPLATE,
        expense=expense,
        employee=employee,
        sender=sender,
        description=Markup(quoted),
    )
    return subject, body

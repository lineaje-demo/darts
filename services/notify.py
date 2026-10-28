"""Reminder emails sent to employees whose expenses need more detail."""

from flask import render_template_string

REMINDER_TEMPLATE = """Hi {{ employee.name.split()[0] }},

Your {{ expense.category|lower }} expense at {{ expense.merchant }} for {{ expense.amount_cents|money }}
on {{ expense.spent_on }} needs more detail before {{ sender.name }} can approve it.

You wrote:

    [[DESCRIPTION]]

Please add an itemised receipt and the business purpose in Darts.

Thanks,
{{ sender.name }}
{{ sender.title }}
"""


def build_reminder(expense, employee, sender) -> tuple[str, str]:
    subject = f"Action needed: your {expense['merchant']} expense"
    # Quote the employee's own words, indented to match the email layout.
    quoted = "\n    ".join((expense["description"] or "(no description)").splitlines())
    source = REMINDER_TEMPLATE.replace("[[DESCRIPTION]]", quoted)
    body = render_template_string(source, expense=expense, employee=employee, sender=sender)
    return subject, body

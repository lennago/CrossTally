"""Whether mails reach anyone, for every app that has to know.

Here and not in 'accounts': the administration of the mail texts lives in
'organisation', and that app does not import from 'accounts'. The answer
depends on the settings only.
"""

from django.conf import settings

#: The backend that writes a mail to the log instead of sending it.
NUR_INS_LOG = "django.core.mail.backends.console.EmailBackend"


def mail_moeglich() -> bool:
    """Whether a mail would reach anyone.

    Without a server it ends in the log ('settings.MAILERS'), and the person
    would wait for a mail that never comes. Everything that promises a mail
    then disappears from the pages: the invitation, "Forgot your password?",
    the paragraphs about mails in the help. During development the log is
    the point, there it counts as sent.
    """
    backend = settings.MAILERS["default"]["BACKEND"]
    return settings.DEBUG or backend != NUR_INS_LOG

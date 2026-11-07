"""Guardian: core of the personal AI agent.

Principles enforced in code (not left to an AI model):
  * Projects are isolated: separate config, credentials, storage, logs, reports.
  * No change to a project without an explicit, single-use approval.
  * No change without a successful backup.
  * Only the verifier can mark an incident as fixed.
  * Anything that could not be checked is reported as NOT_CHECKED with a reason.
"""

__version__ = "0.5.0"

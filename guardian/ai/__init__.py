"""AI brain: a local model (Ollama) that answers free-form questions in
Malayalam or English using read-only Guardian tools.

Safety is enforced in code, not by the model:
  * Only read-only tools exist (plus "start a check", which only reads sites).
    There is no tool to approve, change a site, or read credentials.
  * Tool arguments are validated; unknown tools and projects are refused.
  * Answers must come from tool results (system prompt), tool rounds are
    capped, and only one AI request runs at a time.
  * The model server must be on this machine (loopback address).
"""

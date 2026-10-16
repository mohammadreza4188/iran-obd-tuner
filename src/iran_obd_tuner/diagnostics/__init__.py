"""Vehicle diagnostics: the read-only workflow and its result models.

Import rules for this package:

* may import ``core``, ``hardware`` and ``database``,
* must not import ``tuning``, ``ui`` or ``app``,
* diagnostic interpretation lives here (code structure, descriptions, report assembly);
  raw transport lives in ``hardware``.

Everything in this package is read-only. Nothing here sends a state-changing command: the
only such method declared so far (``ObdAdapter.clear_diagnostic_trouble_codes``) is refused
by the safety policy.
"""

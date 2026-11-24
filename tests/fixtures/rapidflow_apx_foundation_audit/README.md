# Foundation Audit regression witnesses

The four JSON inputs are byte-for-byte copies of the Independent Audit witnesses for AUD-P3-001 through AUD-P3-003. The terminal audit revision was be136f3d4d0ea0b50d51329dbc9a61dd714d719f. In particular, the equivalent Unicode file keeps one raw UTF-8 spelling and one JSON-escaped spelling. The delivery regression adds its explicit harness failpoint to a temporary copy of the original diagnostics input.

The provenance paths are under /tmp/rapidflow_apx_independent_audit/rapidflow_apx_audit_witnesses on the audit host. The repository copies support a self-contained regression on other hosts.

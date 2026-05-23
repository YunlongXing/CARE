from __future__ import annotations

import unittest

from care.analysis.ast_parser import ASTParser
from care.analysis.security_knowledge import SecurityKnowledgeBase

SECURITY_SAMPLE = """#include <stdio.h>
#include <stdlib.h>

int risky(int n, int status) {
    char *buf = malloc(n);
    FILE *fp = fopen("input.txt", "r");
    int fd = open("input.txt", 0);
    mutex_lock(&global_mu);
    if (buf == NULL) {
        return -1;
    }
    if (buf == NULL) {
        return -1;
    }
    if (n < 10) {
        status = -1;
    }
    if (n < 10) {
        status = -1;
    }
    sprintf(buf, "%d", n);
    strcpy(buf, "x");
    goto cleanup;
cleanup:
    fclose(fp);
    close(fd);
    free(buf);
    mutex_unlock(&global_mu);
    return status;
}

int bad_cleanup(void) {
    char *tmp = malloc(8);
    strcpy(tmp, "x");
    return -1;
cleanup:
    free(tmp);
    return 0;
}
"""


class SecurityKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.functions = {
            function.name: function
            for function in ASTParser().parse_file("security.c", SECURITY_SAMPLE)
        }
        self.kb = SecurityKnowledgeBase()

    def test_match_resource_events_covers_requested_patterns(self) -> None:
        events = self.kb.match_resource_events(self.functions["risky"])
        pairs = [(event.kind, event.variable) for event in events]

        self.assertIn(("alloc", "buf"), pairs)
        self.assertIn(("open", "fp"), pairs)
        self.assertIn(("open", "fd"), pairs)
        self.assertIn(("lock", "global_mu"), pairs)
        self.assertIn(("close", "fp"), pairs)
        self.assertIn(("close", "fd"), pairs)
        self.assertIn(("free", "buf"), pairs)
        self.assertIn(("unlock", "global_mu"), pairs)

    def test_match_security_smells_detects_library_patterns(self) -> None:
        smells = self.kb.match_security_smells(self.functions["risky"])
        kinds = {smell["kind"] for smell in smells}

        self.assertIn("unchecked_return_value", kinds)
        self.assertIn("unsafe_function", kinds)
        self.assertIn("repeated_null_checks", kinds)
        self.assertIn("repeated_bounds_checks", kinds)

        unsafe_calls = {
            smell["evidence"]["call"]
            for smell in smells
            if smell["kind"] == "unsafe_function"
        }
        self.assertEqual(unsafe_calls, {"sprintf", "strcpy"})

    def test_error_handling_smells_include_missing_and_unreachable_cleanup(self) -> None:
        smells = self.kb.match_security_smells(self.functions["bad_cleanup"])
        kinds = {smell["kind"] for smell in smells}

        self.assertIn("missing_cleanup_on_error_path", kinds)
        self.assertIn("unreachable_cleanup", kinds)

    def test_get_refactoring_rules_supports_pattern_aliases(self) -> None:
        resource_rules = self.kb.get_refactoring_rules("malloc/free")
        error_rules = self.kb.get_refactoring_rules("goto cleanup")
        validation_rules = self.kb.get_refactoring_rules("repeated null checks")

        self.assertTrue(any("malloc" in rule for rule in resource_rules))
        self.assertTrue(any("goto cleanup" in rule for rule in error_rules))
        self.assertTrue(any("Repeated null" in rule for rule in validation_rules))

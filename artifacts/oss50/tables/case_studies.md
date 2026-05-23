# CARE Paper Case Studies

## Case 1: CARE succeeds where direct LLM-only fails

CARE produced a small validator-passing resource-lifecycle patch, while the direct LLM-only baseline failed on the same detector finding.

- System: CARE
- Project: `cjson`
- Finding: `cjson:000003:resource_imbalance:misc_tests:123:f0b88d835f`
- Function/location: `misc_tests` line `123`
- Description: Resource pointer may have double free.
- Patch path: `benchmarks/oss50/llm-validation-critical-only/patches/cjson/000003-selected.diff`

Validation evidence:

- `apply_patch`: pass. applied patch candidate patch:resource_imbalance:misc_tests:123:f0b88d835f:conservative:0:dd170cc2c2
- `compile`: pass.
- `resource_consistency`: pass. resource scope: misc_tests@oss50/sources/cjson/tests/old_utils_tests.c:88, sort_tests@oss50/sources/cjson/tests/old_utils_tests.c:139
- `security_regression`: pass. compared security facts for 1189 pre-patch and 1189 post-patch functions
- `semantic_equivalence`: pass. compared 1189 pre-patch functions with 1189 post-patch functions
- `static_analysis`: pass.
- `tests`: pass.

LLM-assisted review:

- Verdict/category: `likely_correct`
- Reason: The patch adds a missing cJSON_Delete(object4) call, fixing a resource leak without introducing double free or other resource lifecycle issues. Automated checks show no new resource issues, no semantic or security regressions, and no test failures, though no test execution was performed. The change is a straightforward addition of a missing free, so it is very likely correct and behavior-preserving.

Baseline comparison:

- LLM-only passed validation: `False`
- LLM-only failed stages: `candidate_0`

Diff excerpt:

```diff
--- a/tests/old_utils_tests.c
+++ b/tests/old_utils_tests.c
@@ -133,8 +133,9 @@
     pointer = cJSONUtils_FindPointerFromObjectTo(object3, object4);
     TEST_ASSERT_EQUAL_STRING("/m~1n",pointer);
     free(pointer);
 
     cJSON_Delete(object);
     cJSON_Delete(object1);
     cJSON_Delete(object3);
+    cJSON_Delete(object4);
 }

```

## Case 2: Resource checker prevents an unsafe acceptance

This patch applied cleanly and passed the non-resource stages, but the full validator rejected it because the resource-lifecycle issue remained or worsened.

- System: CARE
- Project: `flac`
- Finding: `flac:000008:resource_imbalance:mutils__free_metadata_blocks:624:5f823f1a79`
- Function/location: `mutils__free_metadata_blocks` line `624`
- Description: Resource data may have double free.
- Patch path: `benchmarks/oss50/llm-validation-critical-only/patches/flac/000008-candidate-001.diff`

Validation evidence:

- `apply_patch`: pass. applied patch candidate patch:resource_imbalance:mutils__free_metadata_blocks:624:5f823f1a79:conservative:0:e01fdc0f2b
- `compile`: pass.
- `resource_consistency`: fail. mutils__free_metadata_blocks: obvious double free of data at lines 614, 624, 625
- `security_regression`: pass. compared security facts for 1671 pre-patch and 1671 post-patch functions
- `semantic_equivalence`: pass. compared 1671 pre-patch functions with 1671 post-patch functions
- `static_analysis`: pass.
- `tests`: pass.

LLM-assisted review:

- Verdict/category: `incorrect`
- Reason: Patch sets cuesheet tracks pointer to NULL after free but does not address double free of data; resource analysis still reports double free issues.

Diff excerpt:

```diff
--- a/src/test_libs_common/metadata_utils.c
+++ b/src/test_libs_common/metadata_utils.c
@@ -618,6 +618,7 @@
 	free(cuesheet->data.cue_sheet.tracks[0].indices);
 	free(cuesheet->data.cue_sheet.tracks[1].indices);
 	free(cuesheet->data.cue_sheet.tracks);
+	cuesheet->data.cue_sheet.tracks = NULL;
 	free(picture->data.picture.mime_type);
 	free(picture->data.picture.description);
 	free(picture->data.picture.data);

```

## Case 3: Direct LLM-only patch fails to apply

The direct LLM baseline often emitted plausible-looking diffs whose hunks did not match the real project context, explaining much of its lower apply rate.

- System: LLM-only
- Project: `sqlite`
- Finding: `sqlite:000010:resource_imbalance:popen2:277:022e191e53`
- Function/location: `popen2` line `277`
- Description: Resource pout may have double close/fclose.
- Patch path: `benchmarks/oss50/llm-validation-critical-llm-only-shards/shard-04/patches/sqlite/000010-candidate-001.diff`

Validation evidence:

- `apply_patch`: fail. command failed (1): git apply --whitespace=nowarn error: home/dragon/CARE/benchmarks/oss50/sources/sqlite/tool/sqlite3_rsync.c: No such file or directory

LLM-assisted review:

- Verdict/category: `incorrect`
- Reason: Patch failed to apply due to missing file; no validation possible.

Diff excerpt:

```diff
--- aoss50/sources/sqlite/tool/sqlite3_rsync.c
+++ boss50/sources/sqlite/tool/sqlite3_rsync.c
@@ -267,14 +267,15 @@
   if( pipe(pout)<0 ){
     close(pin[0]);
     close(pin[1]);
+    /* Do not close pout[0] or pout[1] here: they were not opened on error */
     return 1;
   }
   *pChildPid = fork();
   if( *pChildPid<0 ){
     close(pin[0]);
     close(pin[1]);
     close(pout[0]);
     close(pout[1]);
     *pChildPid = 0;
     return 1;
   }

```

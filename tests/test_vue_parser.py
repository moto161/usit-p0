"""Unit tests for the Vue SFC parser (ring 2, V1)."""
from __future__ import annotations

import unittest

from usit.vue_parser import (
    extract_vue_script,
    extract_vue_template_bindings,
    resolve_vue_bindings,
    vue_script_for_adapter,
)


SETUP_SFC = """<template>
  <button @click="savePhoto">Save</button>
  <form @submit.prevent="onSubmit"><input @input="onInput" /></form>
</template>

<script setup lang="ts">
import { savePhotoApi } from './api';
const savePhoto = async () => { await savePhotoApi(1); };
function onSubmit() { savePhotoApi(2); }
const onInput = () => {};
</script>
"""

OPTIONS_SFC = """<template>
  <button @click="save">Save</button>
</template>

<script>
export default {
  methods: {
    save() { this.$emit('saved'); },
  },
};
</script>
"""


def _symbols(*names_with_lines):
    return [{"name": name, "symbol_id": f"symbol:web/x.vue#{name}:{line}", "line": line}
            for name, line in names_with_lines]


class VueScriptExtractionTests(unittest.TestCase):
    def test_setup_script_preferred_over_plain_script(self):
        content = "<script>export default { methods: { a() {} } };</script>\n<script setup>const b = () => {};</script>"
        script = extract_vue_script(content)
        self.assertIsNotNone(script)
        self.assertIn("const b", script["text"])
        self.assertEqual(script["start_line"], 2)

    def test_plain_script_lang_and_start_line(self):
        script = extract_vue_script(OPTIONS_SFC)
        self.assertIsNotNone(script)
        self.assertEqual(script["lang"], "js")
        self.assertEqual(script["start_line"], 5)
        self.assertIn("methods", script["text"])

    def test_setup_script_lang_ts(self):
        script = extract_vue_script(SETUP_SFC)
        self.assertIsNotNone(script)
        self.assertEqual(script["lang"], "ts")
        self.assertEqual(script["start_line"], 6)

    def test_no_script_returns_none(self):
        self.assertIsNone(extract_vue_script("<template><p>hi</p></template>"))
        self.assertIsNone(vue_script_for_adapter("<template><p>hi</p></template>"))

    def test_padded_script_keeps_file_lines(self):
        padded = vue_script_for_adapter(SETUP_SFC)
        self.assertIsNotNone(padded)
        lines = padded.splitlines()
        self.assertEqual(lines[6], "import { savePhotoApi } from './api';")
        self.assertEqual(lines[7], "const savePhoto = async () => { await savePhotoApi(1); };")


class VueTemplateBindingTests(unittest.TestCase):
    def test_shorthand_and_long_form_with_modifiers(self):
        bindings = extract_vue_template_bindings(SETUP_SFC)
        self.assertEqual([(b["event_name"], b["line"], b["binding_expression"]) for b in bindings],
                         [("click", 2, "savePhoto"), ("submit", 3, "onSubmit"), ("input", 3, "onInput")])

    def test_custom_component_events_are_recorded(self):
        bindings = extract_vue_template_bindings(
            "<template><PhotoCard @update:model-value=\"onUpdate\" @saved=\"onSaved\" /></template>")
        self.assertEqual([b["event_name"] for b in bindings], ["update:model-value", "saved"])
        self.assertEqual(bindings[0]["binding_expression"], "onUpdate")

    def test_single_quotes_and_v_bind_are_not_events(self):
        bindings = extract_vue_template_bindings(
            "<template><button @click='save' :disabled=\"busy\" v-bind:class=\"cls\" /></template>")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["event_name"], "click")

    def test_at_sign_in_text_and_outside_template_is_ignored(self):
        content = ("<template><p>mail me at a@b.com</p></template>\n"
                   "<script>const x = '@click=\"nope\"';</script>")
        self.assertEqual(extract_vue_template_bindings(content), [])

    def test_no_template_returns_empty(self):
        self.assertEqual(extract_vue_template_bindings("<script>const a = 1;</script>"), [])


class VueBindingResolutionTests(unittest.TestCase):
    def test_unique_name_resolves_with_evidence_chain(self):
        symbols = [{"name": name, "symbol_id": f"symbol:web/PhotoCard.vue#{name}:{line}", "line": line}
                   for name, line in (("savePhoto", 8), ("onSubmit", 9), ("onInput", 10))]
        resolved = resolve_vue_bindings(
            extract_vue_template_bindings(SETUP_SFC), symbols, "web/PhotoCard.vue")
        self.assertEqual([b["resolution_status"] for b in resolved],
                         ["RESOLVED", "RESOLVED", "RESOLVED"])
        self.assertEqual(resolved[0]["target"]["id"], "symbol:web/PhotoCard.vue#savePhoto:8")
        self.assertEqual(resolved[0]["evidence_chain"][0]["kind"], "vue_template_event_attribute")
        self.assertEqual(resolved[0]["evidence_chain"][1]["kind"], "handler_definition")

    def test_missing_handler_is_unknown(self):
        resolved = resolve_vue_bindings(
            [{"event_name": "click", "line": 2, "column": 11, "binding_expression": "missing"}],
            _symbols(("savePhoto", 8)), "web/x.vue")
        self.assertEqual(resolved[0]["resolution_status"], "UNKNOWN")
        self.assertNotIn("target", resolved[0])

    def test_duplicate_definitions_are_ambiguous(self):
        resolved = resolve_vue_bindings(
            [{"event_name": "click", "line": 2, "column": 11, "binding_expression": "save"}],
            _symbols(("save", 8), ("save", 20)), "web/x.vue")
        self.assertEqual(resolved[0]["resolution_status"], "AMBIGUOUS")
        self.assertEqual(resolved[0]["candidate_targets"],
                         ["symbol:web/x.vue#save:20", "symbol:web/x.vue#save:8"])

    def test_inline_callback_is_not_symbol_linked(self):
        resolved = resolve_vue_bindings(
            [{"event_name": "click", "line": 2, "column": 11, "binding_expression": "() => save()"}],
            _symbols(("save", 8)), "web/x.vue")
        self.assertEqual(resolved[0]["resolution_status"], "INLINE_CALLBACK_NOT_SYMBOL_LINKED")

    def test_call_expression_is_unknown(self):
        resolved = resolve_vue_bindings(
            [{"event_name": "click", "line": 2, "column": 11, "binding_expression": "save(1)"}],
            _symbols(("save", 8)), "web/x.vue")
        self.assertEqual(resolved[0]["resolution_status"], "UNKNOWN")

    def test_this_member_resolves_by_last_segment(self):
        resolved = resolve_vue_bindings(
            [{"event_name": "click", "line": 2, "column": 11, "binding_expression": "this.save"}],
            _symbols(("save", 8)), "web/x.vue")
        self.assertEqual(resolved[0]["resolution_status"], "RESOLVED")
        self.assertEqual(resolved[0]["target"]["id"], "symbol:web/x.vue#save:8")


if __name__ == "__main__":
    unittest.main()

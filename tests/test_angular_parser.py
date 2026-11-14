"""Tests for Angular template event binding extraction and resolution (A1)."""
from __future__ import annotations

import unittest

from usit.angular_parser import (
    extract_angular_template_bindings,
    find_angular_components,
    find_inline_templates,
    inline_template_for_binding,
    resolve_angular_bindings,
)

_COMPONENT_TS = (
    "import { Component } from '@angular/core';\n"
    "import { HttpClient } from '@angular/common/http';\n"
    "\n"
    "@Component({\n"
    "  selector: 'app-photo-card',\n"
    "  templateUrl: './photo-card.component.html',\n"
    "})\n"
    "export class PhotoCardComponent {\n"
    "  constructor(private http: HttpClient) {}\n"
    "\n"
    "  savePhoto() {\n"
    "    this.http.get('/api/photos').subscribe((rows) => console.log(rows));\n"
    "  }\n"
    "}\n"
)

_TEMPLATE_HTML = (
    "<button (click)=\"savePhoto()\">Save</button>\n"
    "<form (submit)=\"onSubmit($event)\">\n"
    "  <input (input)=\"onInput($event)\" />\n"
    "  <button on-click='savePhoto()'>Old form</button>\n"
    "  <img [src]=\"photo.url\" />\n"
    "  <div *ngIf=\"photo\">x</div>\n"
    "  <!-- <button (click)=\"commentedOut()\">no</button> -->\n"
    "</form>\n"
)

_SYMBOLS = [
    {"name": "PhotoCardComponent", "kind": "class", "line": 8},
    {"name": "savePhoto", "kind": "method", "line": 11},
    {"name": "onSubmit", "kind": "method", "line": 15},
    {"name": "onInput", "kind": "method", "line": 18},
    {"name": "topLevel", "kind": "function", "line": 1},
]


class FindAngularComponentsTests(unittest.TestCase):
    def test_finds_template_url_and_class(self):
        components = find_angular_components(_COMPONENT_TS)
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["class_name"], "PhotoCardComponent")
        self.assertEqual(components[0]["template_url"], "./photo-card.component.html")
        self.assertEqual(components[0]["line"], 4)

    def test_template_url_single_quotes_and_subdirectory(self):
        source = (
            "@Component({ selector: 'app-x', templateUrl: './templates/card.html' })\n"
            "export class CardComponent {}\n"
        )
        components = find_angular_components(source)
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["template_url"], "./templates/card.html")
        self.assertEqual(components[0]["class_name"], "CardComponent")

    def test_inline_template_has_no_component(self):
        source = (
            "@Component({ selector: 'app-x', template: `<button (click)=\"go()\">x</button>` })\n"
            "export class InlineComponent {}\n"
        )
        self.assertEqual(find_angular_components(source), [])

    def test_component_without_template_is_skipped(self):
        source = "@Component({ selector: 'app-x' })\nexport class BareComponent {}\n"
        self.assertEqual(find_angular_components(source), [])

    def test_multiple_components_in_one_file(self):
        source = (
            "@Component({ templateUrl: './a.html' })\nexport class AComponent {}\n"
            "@Component({ templateUrl: './b.html' })\nexport class BComponent {}\n"
        )
        components = find_angular_components(source)
        self.assertEqual([c["class_name"] for c in components],
                         ["AComponent", "BComponent"])
        self.assertEqual([c["template_url"] for c in components],
                         ["./a.html", "./b.html"])


class ExtractAngularTemplateBindingsTests(unittest.TestCase):
    def test_extracts_paren_and_canonical_events(self):
        bindings = extract_angular_template_bindings(_TEMPLATE_HTML)
        self.assertEqual([(b["event_name"], b["line"]) for b in bindings], [
            ("click", 1), ("submit", 2), ("input", 3), ("click", 4),
        ])
        self.assertEqual(bindings[0]["binding_expression"], "savePhoto()")
        self.assertEqual(bindings[1]["binding_expression"], "onSubmit($event)")
        self.assertEqual(bindings[0]["column"], 9)

    def test_property_bindings_and_directives_do_not_match(self):
        bindings = extract_angular_template_bindings(
            '<img [src]="photo.url" />\n<div *ngIf="photo">x</div>\n'
            '<button click="savePhoto()">no parens</button>\n')
        self.assertEqual(bindings, [])

    def test_commented_out_binding_does_not_match(self):
        bindings = extract_angular_template_bindings(
            '<!-- <button (click)="gone()">x</button> -->\n<button (click)="kept()">y</button>\n')
        self.assertEqual([b["binding_expression"] for b in bindings], ["kept()"])

    def test_custom_event_and_key_event_names_kept_verbatim(self):
        bindings = extract_angular_template_bindings(
            '<app-card (photoSaved)="onSaved($event)" (keyup.enter)="onEnter()"></app-card>\n')
        self.assertEqual([b["event_name"] for b in bindings],
                         ["photoSaved", "keyup.enter"])


class ResolveAngularBindingsTests(unittest.TestCase):
    def _resolve(self, expression):
        bindings = extract_angular_template_bindings(
            f'<button (click)="{expression}">x</button>\n')
        return resolve_angular_bindings(
            bindings, _SYMBOLS, "web/photo-card.component.html",
            "web/photo-card.component.ts")[0]

    def test_unique_method_resolves_with_bare_symbol_id(self):
        item = self._resolve("savePhoto()")
        self.assertEqual(item["resolution_status"], "RESOLVED")
        self.assertEqual(item["target"], {
            "id": "symbol:web/photo-card.component.ts#savePhoto:11",
            "path": "web/photo-card.component.ts",
            "name": "savePhoto", "line": 11,
        })
        self.assertEqual([step["kind"] for step in item["evidence_chain"]], [
            "angular_template_event_attribute", "handler_definition",
        ])

    def test_this_prefix_and_event_argument_resolve(self):
        self.assertEqual(self._resolve("this.savePhoto($event)")["resolution_status"],
                         "RESOLVED")
        self.assertEqual(self._resolve("savePhoto")["resolution_status"], "RESOLVED")

    def test_missing_handler_is_unknown(self):
        self.assertEqual(self._resolve("deletePhoto()")["resolution_status"], "UNKNOWN")

    def test_top_level_function_is_not_a_handler(self):
        self.assertEqual(self._resolve("topLevel()")["resolution_status"], "UNKNOWN")

    def test_inline_callback_is_not_symbol_linked(self):
        item = self._resolve("($event) => savePhoto($event)")
        self.assertEqual(item["resolution_status"], "INLINE_CALLBACK_NOT_SYMBOL_LINKED")

    def test_multi_statement_expression_is_unknown(self):
        item = self._resolve("savePhoto(); onSubmit($event)")
        self.assertEqual(item["resolution_status"], "UNKNOWN")

    def test_ambiguous_same_name_methods(self):
        symbols = _SYMBOLS + [
            {"name": "savePhoto", "kind": "method", "line": 30},
        ]
        bindings = extract_angular_template_bindings(
            '<button (click)="savePhoto()">x</button>\n')
        item = resolve_angular_bindings(
            bindings, symbols, "web/photo-card.component.html",
            "web/photo-card.component.ts")[0]
        self.assertEqual(item["resolution_status"], "AMBIGUOUS")
        self.assertEqual(item["candidate_targets"], [
            "symbol:web/photo-card.component.ts#savePhoto:11",
            "symbol:web/photo-card.component.ts#savePhoto:30",
        ])


class FindInlineTemplatesTests(unittest.TestCase):
    _INLINE_COMPONENT_TS = (
        "import { Component } from '@angular/core';\n"
        "import { HttpClient } from '@angular/common/http';\n"
        "\n"
        "@Component({\n"
        "  selector: 'app-photo-card',\n"
        "  template: `\n"
        "    <button (click)=\"savePhoto()\">Save</button>\n"
        "  `,\n"
        "})\n"
        "export class PhotoCardComponent {\n"
        "  constructor(private http: HttpClient) {}\n"
        "\n"
        "  savePhoto() {\n"
        "    this.http.get('/api/photos').subscribe();\n"
        "  }\n"
        "}\n"
    )

    def test_finds_backtick_multiline_inline_template(self):
        templates = find_inline_templates(self._INLINE_COMPONENT_TS)
        self.assertEqual(len(templates), 1)
        template = templates[0]
        self.assertEqual(template["class_name"], "PhotoCardComponent")
        self.assertEqual(template["text"],
                         "\n    <button (click)=\"savePhoto()\">Save</button>\n  ")
        self.assertEqual((template["start_line"], template["start_column"]), (6, 14))
        self.assertEqual(template["line"], 4)

    def test_single_quote_single_line_template(self):
        source = (
            "@Component({ selector: 'app-x', template: '<button (click)=\"go()\">x</button>' })\n"
            "export class InlineComponent {}\n"
        )
        templates = find_inline_templates(source)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["text"], '<button (click)="go()">x</button>')
        self.assertEqual((templates[0]["start_line"], templates[0]["start_column"]),
                         (1, 44))

    def test_template_url_component_has_no_inline_template(self):
        self.assertEqual(find_inline_templates(_COMPONENT_TS), [])

    def test_non_literal_template_is_skipped(self):
        source = (
            "@Component({ selector: 'app-x', template: MY_TEMPLATE })\n"
            "export class RefComponent {}\n"
        )
        self.assertEqual(find_inline_templates(source), [])

    def test_multiple_inline_components_in_one_file(self):
        source = (
            "@Component({ template: `<button (click)=\"a()\">x</button>` })\n"
            "export class AComponent {}\n"
            "@Component({ template: `<button (click)=\"b()\">y</button>` })\n"
            "export class BComponent {}\n"
        )
        templates = find_inline_templates(source)
        self.assertEqual([t["class_name"] for t in templates],
                         ["AComponent", "BComponent"])

    def test_template_key_outside_decorator_is_ignored(self):
        source = "const cfg = { template: '<button (click)=\"x()\">y</button>' };\n"
        self.assertEqual(find_inline_templates(source), [])

    def test_padded_binding_lines_map_to_file_lines(self):
        template = find_inline_templates(self._INLINE_COMPONENT_TS)[0]
        padded = inline_template_for_binding(
            template["text"], template["start_line"], template["start_column"])
        bindings = extract_angular_template_bindings(padded)
        self.assertEqual(len(bindings), 1)
        self.assertEqual((bindings[0]["line"], bindings[0]["column"]), (7, 13))
        self.assertEqual(bindings[0]["binding_expression"], "savePhoto()")

    def test_inline_binding_resolves_to_component_method(self):
        template = find_inline_templates(self._INLINE_COMPONENT_TS)[0]
        padded = inline_template_for_binding(
            template["text"], template["start_line"], template["start_column"])
        symbols = [
            {"name": "PhotoCardComponent", "kind": "class", "line": 10},
            {"name": "savePhoto", "kind": "method", "line": 13},
        ]
        item = resolve_angular_bindings(
            extract_angular_template_bindings(padded), symbols,
            "web/photo-card.component.ts", "web/photo-card.component.ts")[0]
        self.assertEqual(item["resolution_status"], "RESOLVED")
        self.assertEqual(item["target"]["id"],
                         "symbol:web/photo-card.component.ts#savePhoto:13")
        self.assertEqual(item["target"]["path"], "web/photo-card.component.ts")


if __name__ == "__main__":
    unittest.main()

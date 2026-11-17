import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";
import dashboard from "./ru/dashboard";
import onboarding from "./ru/onboarding";
import workflow from "./ru/workflow";

type Messages = Record<string, unknown>;
const en = import.meta.glob<Messages>("./en/*.ts", { eager: true, import: "default" });
const ru = import.meta.glob<Messages>("./ru/*.ts", { eager: true, import: "default" });

function flatten(messages: Messages, prefix = ""): Record<string, string> {
  return Object.fromEntries(Object.entries(messages).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === "string"
      ? [[path, value]]
      : Object.entries(flatten(value as Messages, path));
  }));
}

function placeholders(text: string): string[] {
  return [...new Set(text.match(/\{\{[^}]+\}\}|\[\[brand\]\]/g) ?? [])].sort();
}

describe("Russian locale", () => {
  it("covers every namespace and key, including Russian plural categories", () => {
    expect(Object.keys(ru).map((path) => path.replace("/ru/", "/en/")).sort())
      .toEqual(Object.keys(en).sort());

    for (const [path, messages] of Object.entries(en)) {
      const english = flatten(messages);
      const russian = flatten(ru[path.replace("/en/", "/ru/")]);
      const expected = new Set(Object.keys(english));
      for (const key of Object.keys(english)) {
        if (key.endsWith("_one") && `${key.slice(0, -4)}_other` in english) {
          for (const category of new Intl.PluralRules("ru").resolvedOptions().pluralCategories) {
            expected.add(`${key.slice(0, -4)}_${category}`);
          }
        }
      }
      expect({ path, keys: Object.keys(russian).sort() }).toEqual({ path, keys: [...expected].sort() });
    }
  });

  it("preserves interpolation and brand placeholders in every translation", () => {
    const mismatches: string[] = [];
    for (const [path, messages] of Object.entries(en)) {
      const english = flatten(messages);
      const russian = flatten(ru[path.replace("/en/", "/ru/")]);
      for (const [key, text] of Object.entries(russian)) {
        const source = english[key] ?? english[key.replace(/_(few|many)$/, "_other")];
        if (source === undefined) {
          mismatches.push(`${path}:${key}`);
          continue;
        }
        const expected = placeholders(source);
        let actual = placeholders(text);
        const pluralBase = key.replace(/_(one|few|many|other)$/, "");
        // Russian singular forms also cover 21 and 31, where English may spell out "this".
        if (`${pluralBase}_one` in english
          && placeholders(english[`${pluralBase}_other`]).includes("{{count}}")
          && !expected.includes("{{count}}")) {
          actual = actual.filter((token) => token !== "{{count}}");
        }
        if (JSON.stringify(actual) !== JSON.stringify(expected)) mismatches.push(`${path}:${key}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  it("uses the visible connectivity button label in the provider tour", () => {
    expect(onboarding.settings_providers_body).toContain(`«${dashboard.connectivity_check}»`);
  });

  it("selects Russian plural forms and interpolates asset counts", async () => {
    const instance = createInstance();
    await instance.init({ lng: "ru", resources: { ru: { workflow, dashboard } }, defaultNS: "workflow" });

    for (const [count, category] of [[1, "one"], [2, "few"], [5, "many"], [21, "one"], [1.5, "other"]] as const) {
      const key = `status_assets_missing_${category}` as const;
      expect(instance.t("status_assets_missing", { count }))
        .toBe(workflow[key].replace("{{count}}", String(count)));
    }
    expect(instance.t("reference_batch_enqueue_interrupted_intro", { ns: "dashboard", count: 21 }))
      .toContain("21 видеофрагмент");
  });
});

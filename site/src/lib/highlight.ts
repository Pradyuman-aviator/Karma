import { codeToHtml } from "shiki";

/** Syntax-highlighted HTML at build time, with both light and dark colours. */
export async function highlight(code: string, lang: string): Promise<string> {
  return codeToHtml(code.trimEnd(), {
    lang,
    themes: { light: "github-light", dark: "github-dark-dimmed" },
    defaultColor: false,
  });
}

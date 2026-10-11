import { Zap } from "lucide-react";

import { GithubIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { REPO } from "@/lib/utils";

export function CallToAction() {
  return (
    <section className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
      <div className="relative overflow-hidden rounded-3xl border bg-terminal px-6 py-14 text-center text-terminal-foreground sm:px-12">
        <div className="pointer-events-none absolute -top-24 left-1/2 h-64 w-[640px] -translate-x-1/2 rounded-full bg-[#f0a03a]/20 blur-3xl" />
        <h2 className="relative text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
          Stop waiting for tests your change can&apos;t break.
        </h2>
        <p className="relative mx-auto mt-4 max-w-xl text-[#9fb2b8]">
          Free and open source, MIT licensed. Check it on your own code first: the safety script
          breaks functions one at a time and proves nothing is missed.
        </p>
        <div className="relative mt-8 flex flex-wrap justify-center gap-3">
          <Button size="lg" asChild>
            <a href="#setup">Get started</a>
          </Button>
          <Button
            size="lg"
            variant="outline"
            className="border-white/15 bg-white/5 text-terminal-foreground hover:bg-white/10 hover:text-white"
            asChild
          >
            <a href={REPO}>
              <GithubIcon className="size-4" /> Star on GitHub
            </a>
          </Button>
        </div>
      </div>
    </section>
  );
}

export function Footer({ version }: { version: string }) {
  return (
    <footer className="border-t">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-10 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="flex items-center gap-2">
          <Zap className="size-4 text-amber" /> Karma {version} · MIT licensed
        </p>
        <nav className="flex flex-wrap gap-x-5 gap-y-2">
          <a className="hover:text-foreground" href={REPO}>GitHub</a>
          <a className="hover:text-foreground" href={`${REPO}/blob/main/CHANGELOG.md`}>Changelog</a>
          <a className="hover:text-foreground" href={`${REPO}/blob/main/CONTRIBUTING.md`}>Contributing</a>
          <a className="hover:text-foreground" href={`${REPO}/issues`}>Issues</a>
        </nav>
      </div>
    </footer>
  );
}

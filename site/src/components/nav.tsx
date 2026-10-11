"use client";

import { Menu, Moon, Sun, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { GithubIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { cn, REPO } from "@/lib/utils";

const LINKS = [
  { href: "#demo", label: "Function-level" },
  { href: "#simulation", label: "Simulation" },
  { href: "#how", label: "How it works" },
  { href: "#proof", label: "Proof" },
  { href: "#research", label: "Research" },
  { href: "#features", label: "Features" },
  { href: "#setup", label: "Setup" },
  { href: "#reference", label: "Reference" },
];



export function Nav({ version }: { version: string }) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useState(true);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("karma-theme", next ? "dark" : "light");
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  };

  return (
    <header
      className={cn(
        "sticky top-0 z-40 w-full transition-colors",
        scrolled || open ? "border-b bg-background/80 backdrop-blur-lg" : "border-b border-transparent",
      )}
    >
      <nav className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4 sm:px-6">
        <a href="#top" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="flex size-7 items-center justify-center rounded-md bg-terminal text-amber">
            <Zap className="size-4 fill-current" />
          </span>
          Karma
          <span className="rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
            v{version}
          </span>
        </a>
        <div className="hidden flex-1 items-center gap-1 lg:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </a>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1 lg:ml-0">
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle colour theme">
            {dark ? <Sun /> : <Moon />}
          </Button>
          <Button variant="ghost" size="icon" asChild>
            <a href={REPO} aria-label="Karma on GitHub">
              <GithubIcon className="size-4" />
            </a>
          </Button>
          <Button size="sm" className="hidden sm:inline-flex" asChild>
            <a href="#setup">Get started</a>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setOpen(!open)}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
          >
            {open ? <X /> : <Menu />}
          </Button>
        </div>
      </nav>
      {open ? (
        <div className="border-t px-4 pb-4 lg:hidden">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              onClick={() => setOpen(false)}
              className="block rounded-md px-2 py-2.5 text-sm text-muted-foreground hover:text-foreground"
            >
              {link.label}
            </a>
          ))}
        </div>
      ) : null}
    </header>
  );
}

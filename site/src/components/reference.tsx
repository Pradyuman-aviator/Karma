"use client";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Command } from "@/lib/data";

type Props = {
  commands: Command[];
  inputs: { name: string; description: string; default: string }[];
  outputs: { name: string; description: string }[];
  config: { key: string; type: string; default: string; description: string }[];
};

/** Strip the reStructuredText-style backticks the Action's descriptions use. */
function plain(text: string) {
  return text.replace(/`([^`]+)`/g, "$1");
}

export function Reference({ commands, inputs, outputs, config }: Props) {
  return (
    <Tabs defaultValue="commands">
      <TabsList>
        <TabsTrigger value="commands">Commands</TabsTrigger>
        <TabsTrigger value="action">GitHub Action</TabsTrigger>
        <TabsTrigger value="config">Configuration</TabsTrigger>
      </TabsList>

      <TabsContent value="commands">
        <Accordion type="single" collapsible defaultValue="run" className="rounded-xl border bg-card px-5">
          {commands.map((command) => (
            <AccordionItem key={command.name} value={command.name} className="last:border-0">
              <AccordionTrigger>
                <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <code className="font-mono text-sm text-amber">karma {command.name}</code>
                  <span className="text-sm font-normal text-muted-foreground">{command.help}</span>
                </span>
              </AccordionTrigger>
              <AccordionContent>
                <p className="text-foreground/90">{command.description}</p>
                {command.examples.length ? (
                  <ul className="mt-4 space-y-1.5">
                    {command.examples.map((e) => (
                      <li key={e.command} className="grid gap-x-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                        <code className="break-all font-mono text-xs text-foreground">{e.command}</code>
                        <span className="text-xs">{e.what}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <OptionsTable command={command} />
                {command.actions.map((action) => (
                  <div key={action.name} className="mt-4">
                    <code className="font-mono text-xs text-amber">karma {action.name}</code>
                    <span className="ml-2 text-xs">{action.help}</span>
                    <OptionsTable command={action} />
                  </div>
                ))}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </TabsContent>

      <TabsContent value="action">
        <Table
          head={["Input", "Default", "Description"]}
          rows={inputs.map((i) => [i.name, i.default || "—", plain(i.description)])}
        />
        <Table
          className="mt-4"
          head={["Output", "Description"]}
          rows={outputs.map((o) => [o.name, plain(o.description)])}
        />
      </TabsContent>

      <TabsContent value="config">
        <Table
          head={["[tool.karma] key", "Type", "Default", "Description"]}
          rows={config.map((c) => [c.key, c.type, c.default, c.description])}
        />
      </TabsContent>
    </Tabs>
  );
}

function OptionsTable({ command }: { command: Command }) {
  const options = command.options.filter((o) => !o.positional);
  if (!options.length) return null;
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full min-w-[520px] text-xs">
        <tbody>
          {options.map((o) => (
            <tr key={o.flags.join()} className="border-t">
              <td className="whitespace-nowrap py-2 pr-4 align-top font-mono text-foreground">
                {o.flags.join(", ")}
                {o.metavar ? <span className="text-muted-foreground"> {o.metavar}</span> : null}
                {o.choices ? <span className="text-muted-foreground"> {"{" + o.choices.join(",") + "}"}</span> : null}
              </td>
              <td className="py-2 align-top">{o.help}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Table({ head, rows, className }: { head: string[]; rows: string[][]; className?: string }) {
  return (
    <div className={`overflow-x-auto rounded-xl border bg-card ${className ?? ""}`}>
      <table className="w-full min-w-[560px] text-sm">
        <thead>
          <tr className="border-b text-left">
            {head.map((h) => (
              <th key={h} className="p-3 font-medium text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[0]} className="border-b align-top last:border-0">
              {row.map((cell, i) => (
                <td key={i} className={i === 0 ? "whitespace-nowrap p-3 font-mono text-xs text-amber" : i < row.length - 1 ? "p-3 font-mono text-xs text-muted-foreground" : "p-3 text-muted-foreground"}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

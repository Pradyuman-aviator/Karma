import { DemoSection } from "@/components/demo";
import { Features } from "@/components/features";
import { CallToAction, Footer } from "@/components/footer";
import { GraphLab } from "@/components/graph-lab";
import { Hero } from "@/components/hero";
import { How } from "@/components/how";
import { Nav } from "@/components/nav";
import { Proof } from "@/components/proof";
import { Reference } from "@/components/reference";
import { Research } from "@/components/research";
import { Section } from "@/components/section";
import { Setup } from "@/components/setup";
import { Simulation } from "@/components/simulation";
import { data } from "@/lib/data";

export default function Home() {
  return (
    <>
      <Nav version={data.version} />
      <main>
        <Hero />
        <DemoSection />
        <div className="border-y bg-card/30">
          <Section
            id="simulation"
            eyebrow="Simulation"
            title={
              <>
                Watch Karma think, <span className="text-amber">step by step</span>
              </>
            }
            intro="Pick a change and press play. Each step is what Karma really does, replayed from its real output for the demo project: read the diff, compare units, follow the names wave by wave, select, run."
          >
            <Simulation demo={data.demo} />
          </Section>
        </div>
        <Section
          id="graph"
          eyebrow="Dependency graph"
          title={
            <>
              See the <span className="text-amber">blast radius</span> of any file
            </>
          }
          intro="This is Karma's real import graph of starlette (karma graph --format json). Click any file to change it and watch the impact ripple outwards through everything that imports it, wave by wave, until it reaches the tests. Hover a file to see what it imports and what imports it."
        >
          <GraphLab samples={data.samples} />
        </Section>
        <div className="border-y bg-card/30">
          <How />
        </div>
        <Proof />
        <div className="border-y bg-card/30">
          <Research />
        </div>
        <Features />
        <div className="border-y bg-card/30">
          <Setup />
        </div>
        <Section
          id="reference"
          eyebrow="Reference"
          title="Every command, input and option"
          intro="Generated from Karma's own argument parser, action.yml and configuration schema, and checked by its test suite, so this page always matches the tool."
        >
          <Reference
            commands={data.commands}
            inputs={data.action.inputs}
            outputs={data.action.outputs}
            config={data.config}
          />
        </Section>
        <CallToAction />
      </main>
      <Footer version={data.version} />
    </>
  );
}

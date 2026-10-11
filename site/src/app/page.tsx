import { DemoSection } from "@/components/demo";
import { Features } from "@/components/features";
import { CallToAction, Footer } from "@/components/footer";
import { Hero } from "@/components/hero";
import { How } from "@/components/how";
import { Nav } from "@/components/nav";
import { Proof } from "@/components/proof";
import { Reference } from "@/components/reference";
import { Research } from "@/components/research";
import { Section } from "@/components/section";
import { Setup } from "@/components/setup";
import { data } from "@/lib/data";

export default function Home() {
  return (
    <>
      <Nav version={data.version} />
      <main>
        <Hero />
        <DemoSection />
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

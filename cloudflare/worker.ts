import { DurableObject } from "cloudflare:workers";
import pLimit from "p-limit";

const PORT = 8000;
type SavedSnapshot = { image: string; snapshot: ContainerSnapshot };

// One shared fictional business, matching the local single-process application.
export class Omada extends DurableObject<Env> {
  private starting?: Promise<void>;
  private snapshots = pLimit(1);

  private async ready(): Promise<void> {
    this.starting ??= this.start().finally(() => { this.starting = undefined; });
    await this.starting;
  }

  private async start(): Promise<void> {
    const container = this.ctx.container!;
    if (!container.running) {
      const saved = await this.ctx.storage.get<SavedSnapshot>("snapshot");
      const image = container.images.app;
      const source = saved?.image === image
        ? { containerSnapshot: saved.snapshot }
        : { image };
      container.start({
        ...source,
        instance: "lite",
        enableInternet: true,
        env: {
          ANTHROPIC_API_KEY: this.env.ANTHROPIC_API_KEY,
          LLM_MODEL: this.env.LLM_MODEL,
          LLM_MODE: "live",
          DESK_DB: "/app/data/desk.sqlite3",
          PYTHONUNBUFFERED: "1",
        },
      });
    }
    await container.setInactivityTimeout(10 * 60 * 1000);
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const response = await container.getTcpPort(PORT).fetch("http://container/api/health", {
          signal: AbortSignal.timeout(1000),
        });
        await response.body?.cancel();
        if (response.ok) return;
      } catch { /* The Python server is still starting. */ }
      await scheduler.wait(200);
    }
    throw new Error("Omada did not become ready");
  }

  private async checkpoint(): Promise<void> {
    await this.snapshots(async () => {
      const snapshot = await this.ctx.container!.snapshotContainer({ name: "omada-demo" });
      await this.ctx.storage.put("snapshot", {
        image: this.ctx.container!.images.app, snapshot,
      } satisfies SavedSnapshot);
    });
  }

  async fetch(request: Request): Promise<Response> {
    await this.ready();
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    const response = await this.ctx.container!.getTcpPort(PORT).fetch(forwarded);
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method) && response.ok) {
      await this.checkpoint();
    }
    return response;
  }

  async scheduled(): Promise<void> {
    await this.ready();
    await this.checkpoint();
  }
}

export default {
  fetch(request, env) {
    return env.DESK.getByName("shared-demo").fetch(request);
  },
  async scheduled(_event, env) {
    await env.DESK.getByName("shared-demo").scheduled();
  },
} satisfies ExportedHandler<Env>;

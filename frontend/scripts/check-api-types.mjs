// Fails when src/api/schema.d.ts is stale relative to docs/contracts/openapi.json.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "api-types-")), "schema.d.ts");
execFileSync("npx", ["openapi-typescript", "../docs/contracts/openapi.json", "-o", out], {
  stdio: "ignore",
});
if (readFileSync(out, "utf8") !== readFileSync("src/api/schema.d.ts", "utf8")) {
  console.error("src/api/schema.d.ts is stale; run: npm run gen:api");
  process.exit(1);
}

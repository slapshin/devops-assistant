// Fails when src/api/schema.d.ts is stale relative to docs/contracts/openapi.json.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const OPENAPI_PATH = "../docs/contracts/openapi.json";
const COMMITTED_TYPES_PATH = "src/api/schema.d.ts";

const generatedTypesPath = join(mkdtempSync(join(tmpdir(), "api-types-")), "schema.d.ts");
execFileSync("npx", ["openapi-typescript", OPENAPI_PATH, "-o", generatedTypesPath], { stdio: "ignore" });

if (readFileSync(generatedTypesPath, "utf8") !== readFileSync(COMMITTED_TYPES_PATH, "utf8")) {
  console.error(`${COMMITTED_TYPES_PATH} is stale relative to ${OPENAPI_PATH}; run: npm run gen:api`);
  process.exit(1);
}

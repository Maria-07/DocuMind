import { execFileSync } from "node:child_process";
import path from "node:path";

export const FIXTURES = path.join(__dirname, ".fixtures");

export default function globalSetup() {
  const python =
    process.env.BACKEND_PYTHON ??
    path.join(__dirname, "..", "..", "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  execFileSync(python, [path.join(__dirname, "make_fixtures.py"), FIXTURES], { stdio: "inherit" });
}

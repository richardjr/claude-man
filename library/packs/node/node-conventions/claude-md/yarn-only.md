# Yarn only — never npm

- Use `yarn` for everything: `yarn install`, `yarn add`, `yarn <script>`. Never run `npm` or
  `npx` in a yarn project — mixed lockfiles/`node_modules` corrupt the install (some projects
  enforce this with a preinstall guard).
- Respect the `packageManager` field in package.json (corepack pins the yarn version); don't
  upgrade or work around it.
- In a workspaces monorepo, run package-scoped commands with `yarn workspace <name> <script>`
  from the root rather than `cd`-ing into packages; let the workspace resolver handle
  cross-package deps (`workspace:*`) — no manual `yarn link`.
- Don't edit a lockfile by hand, and don't regenerate it wholesale to "fix" one dependency.

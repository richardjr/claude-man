-- claude-man curated plugin setup — TypeScript + Markdown + git-from-nvim.
--
-- Runs after packloadall (the baked pack/curated/start plugins are loaded, their plugin/ scripts
-- sourced, their lua/ modules requireable). Each setup is pcall-guarded so one plugin's failure can't
-- break the whole config. See init.lua for the layout/rationale.

local function safe(name, fn)
  local ok, err = pcall(fn)
  if not ok then
    vim.notify("curated nvim: " .. name .. " setup failed: " .. tostring(err), vim.log.levels.WARN)
  end
end

-- Colorscheme (matteblack — matches your local look; silently keeps the default if unavailable).
pcall(vim.cmd.colorscheme, "matteblack")

-- Treesitter: highlighting + indentation. Parsers are baked (/opt/nvim-parsers); NEVER auto-install
-- at runtime (the rootfs is read-only and there is no compiler).
safe("treesitter", function()
  require("nvim-treesitter.configs").setup({
    parser_install_dir = "/opt/nvim-parsers",
    ensure_installed = {},
    auto_install = false,
    highlight = { enable = true },
    indent = { enable = true },
  })
end)

-- Completion (nvim-cmp; native snippet expansion — no LuaSnip C dependency).
safe("cmp", function()
  local cmp = require("cmp")
  cmp.setup({
    snippet = { expand = function(args) vim.snippet.expand(args.body) end },
    sources = cmp.config.sources({ { name = "nvim_lsp" }, { name = "path" } }, { { name = "buffer" } }),
    mapping = cmp.mapping.preset.insert({
      ["<C-Space>"] = cmp.mapping.complete(),
      ["<CR>"] = cmp.mapping.confirm({ select = true }),
      ["<C-n>"] = cmp.mapping.select_next_item(),
      ["<C-p>"] = cmp.mapping.select_prev_item(),
    }),
  })
end)

-- LSP: ts_ls (TypeScript), marksman (Markdown), jsonls (JSON) — all baked on PATH. nvim-lspconfig (on
-- the runtimepath) ships the server configs as lsp/<name>.lua for neovim's NATIVE LSP API (0.11+); we
-- set default completion capabilities globally and enable the servers. (The old
-- require('lspconfig')[name].setup() framework is deprecated on 0.11+ and is intentionally avoided.)
safe("lsp", function()
  local ok, cmp_lsp = pcall(require, "cmp_nvim_lsp")
  if ok then
    vim.lsp.config("*", { capabilities = cmp_lsp.default_capabilities() })
  end
  vim.lsp.enable({ "ts_ls", "marksman", "jsonls" })
end)

vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local function map(lhs, rhs, desc) vim.keymap.set("n", lhs, rhs, { buffer = ev.buf, desc = desc }) end
    map("gd", vim.lsp.buf.definition, "goto definition")
    map("gr", vim.lsp.buf.references, "references")
    map("K", vim.lsp.buf.hover, "hover")
    map("<leader>rn", vim.lsp.buf.rename, "rename")
    map("<leader>ca", vim.lsp.buf.code_action, "code action")
    map("[d", function() vim.diagnostic.jump({ count = -1 }) end, "prev diagnostic")
    map("]d", function() vim.diagnostic.jump({ count = 1 }) end, "next diagnostic")
  end,
})

-- Formatting: prettier via conform on <leader>f (NOT on save — avoid surprising reformats).
safe("conform", function()
  require("conform").setup({
    formatters_by_ft = {
      typescript = { "prettier" }, typescriptreact = { "prettier" },
      javascript = { "prettier" }, javascriptreact = { "prettier" },
      json = { "prettier" }, jsonc = { "prettier" }, markdown = { "prettier" },
      css = { "prettier" }, html = { "prettier" }, yaml = { "prettier" },
    },
  })
  vim.keymap.set("n", "<leader>f",
    function() require("conform").format({ async = true, lsp_format = "fallback" }) end,
    { desc = "format buffer" })
end)

-- Markdown in-buffer rendering.
safe("render-markdown", function() require("render-markdown").setup({}) end)

-- Git: gitsigns (hunks/blame) + fugitive (:Git commit/push). claude-man injects your git identity, so
-- commits made here carry the right author.
safe("gitsigns", function() require("gitsigns").setup({}) end)
vim.keymap.set("n", "<leader>gg", "<cmd>Git<cr>", { desc = "git status (fugitive)" })
vim.keymap.set("n", "<leader>gc", "<cmd>Git commit<cr>", { desc = "git commit" })
vim.keymap.set("n", "<leader>gp", "<cmd>Git push<cr>", { desc = "git push" })
vim.keymap.set("n", "<leader>gb", "<cmd>Gitsigns blame_line<cr>", { desc = "blame line" })
vim.keymap.set("n", "]h", "<cmd>Gitsigns next_hunk<cr>", { desc = "next hunk" })
vim.keymap.set("n", "[h", "<cmd>Gitsigns prev_hunk<cr>", { desc = "prev hunk" })
vim.keymap.set("n", "<leader>hs", "<cmd>Gitsigns stage_hunk<cr>", { desc = "stage hunk" })
vim.keymap.set("n", "<leader>hp", "<cmd>Gitsigns preview_hunk<cr>", { desc = "preview hunk" })

-- Niceties.
vim.keymap.set("n", "<leader>w", "<cmd>w<cr>", { desc = "save" })
vim.keymap.set("n", "<Esc>", "<cmd>nohlsearch<cr>")

-- claude-man baked neovim config (read-only at runtime).
--
-- Curated for TypeScript + Markdown + git-from-nvim under the hardened container. NO plugin manager:
-- plugins are baked at ~/.config/nvim/pack/curated/start/<plugin> (native packages, auto-loaded by
-- packloadall) and treesitter parsers at /opt/nvim-parsers (compiled at image build). LSP servers
-- (ts_ls / marksman / jsonls) + prettier are on PATH. nvim's only runtime writes — shada, swap, undo,
-- state, cache — land on the writable .cache tmpfs (XDG_STATE_HOME=~/.cache/state, XDG_CACHE_HOME).
--
-- Options + leader live here; plugin SETUP lives in after/plugin/curated.lua, which runs AFTER
-- packloadall (so the baked plugins are loaded and `require()` resolves).

vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

-- Baked treesitter parsers live outside the config tree (compiled into /opt/nvim-parsers at build).
vim.opt.runtimepath:append("/opt/nvim-parsers")

local o = vim.opt
o.number = true
o.relativenumber = true
o.expandtab = true
o.shiftwidth = 2
o.tabstop = 2
o.smartindent = true
o.ignorecase = true
o.smartcase = true
o.termguicolors = true
o.signcolumn = "yes"
o.cursorline = true
o.scrolloff = 4
o.updatetime = 300
o.undofile = true -- persists to stdpath('state') = ~/.cache/state (writable tmpfs)
o.clipboard = "unnamedplus"
o.mouse = "a"
o.wrap = false

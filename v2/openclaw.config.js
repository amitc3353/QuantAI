// QuantAI v2 — OpenClaw Multi-Agent Configuration
// Copy to: /root/quantai-v2/.openclaw/config.js (or ~/.openclaw/openclaw.js)
//
// Requires these env vars (set in .env):
//   DISCORD_TOKEN_ORCHESTRATOR, DISCORD_TOKEN_RESEARCH,
//   DISCORD_TOKEN_INFRA, DISCORD_TOKEN_JOURNAL,
//   DISCORD_GUILD_ID, DISCORD_CHANNEL_CHAT, DISCORD_CHANNEL_RESEARCH,
//   DISCORD_CHANNEL_INFRA, DISCORD_CHANNEL_JOURNAL, DISCORD_CHANNEL_ALERTS

const path = require('path');
const base = process.env.QUANTAI_HOME || '/root/quantai-v2';

module.exports = {
  agents: {
    list: [
      {
        id: 'orchestrator',
        workspace: path.join(base, 'workspace-orchestrator'),
        model: 'claude-sonnet-4-20250514',
        maxTurns: 50,
      },
      {
        id: 'research',
        workspace: path.join(base, 'workspace-research'),
        model: 'claude-sonnet-4-20250514',
        maxTurns: 30,
      },
      {
        id: 'infra',
        workspace: path.join(base, 'workspace-infra'),
        model: 'claude-sonnet-4-20250514',
        maxTurns: 40,
        allowedTools: ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep'],
      },
      {
        id: 'journal',
        workspace: path.join(base, 'workspace-journal'),
        model: 'claude-haiku-4-5-20251001',
        maxTurns: 20,
      },
    ],
  },

  bindings: [
    { agentId: 'orchestrator', match: { channel: 'discord', accountId: 'orchestrator' } },
    { agentId: 'research',     match: { channel: 'discord', accountId: 'research' } },
    { agentId: 'infra',        match: { channel: 'discord', accountId: 'infra' } },
    { agentId: 'journal',      match: { channel: 'discord', accountId: 'journal' } },
  ],

  channels: {
    discord: {
      groupPolicy: 'allowlist',
      accounts: {
        orchestrator: {
          token: process.env.DISCORD_TOKEN_ORCHESTRATOR,
          guilds: {
            [process.env.DISCORD_GUILD_ID]: {
              channels: {
                [process.env.DISCORD_CHANNEL_CHAT]:   { allow: true, requireMention: false },
                [process.env.DISCORD_CHANNEL_ALERTS]: { allow: true, requireMention: true },
              },
            },
          },
        },
        research: {
          token: process.env.DISCORD_TOKEN_RESEARCH,
          guilds: {
            [process.env.DISCORD_GUILD_ID]: {
              channels: {
                [process.env.DISCORD_CHANNEL_RESEARCH]: { allow: true, requireMention: false },
              },
            },
          },
        },
        infra: {
          token: process.env.DISCORD_TOKEN_INFRA,
          guilds: {
            [process.env.DISCORD_GUILD_ID]: {
              channels: {
                [process.env.DISCORD_CHANNEL_INFRA]: { allow: true, requireMention: false },
              },
            },
          },
        },
        journal: {
          token: process.env.DISCORD_TOKEN_JOURNAL,
          guilds: {
            [process.env.DISCORD_GUILD_ID]: {
              channels: {
                [process.env.DISCORD_CHANNEL_JOURNAL]: { allow: true, requireMention: false },
              },
            },
          },
        },
      },
    },
  },
};

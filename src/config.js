import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = join(dirname(fileURLToPath(import.meta.url)), '..');
const envFile = join(rootDir, '.env');

if (existsSync(envFile) && typeof process.loadEnvFile === 'function') {
  try {
    process.loadEnvFile(envFile);
  } catch {
    // .env malformado: segue com as variaveis do ambiente
  }
}

function text(name, fallback = '') {
  return (process.env[name] ?? fallback).trim();
}

function number(name, fallback) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? value : fallback;
}

function flag(name, fallback) {
  const value = text(name).toLowerCase();
  if (!value) return fallback;
  return value === '1' || value === 'true' || value === 'yes' || value === 'on';
}

export const config = {
  rootDir,
  botToken: text('BOT_TOKEN'),
  ownerId: text('OWNER_ID'),
  guildId: text('GUILD_ID'),
  dataDir: join(rootDir, 'data'),
  balanceFile: join(rootDir, 'data', 'balance.json'),
  accountsFile: join(rootDir, 'data', 'accounts.json'),
  accountsKeyFile: join(rootDir, 'data', '.accounts.key'),
  apiBase: 'https://discord.com/api/v9',
  clientBuildNumber: number('CLIENT_BUILD_NUMBER', 548000),
  videoSpeed: number('VIDEO_SPEED', 7),
  videoIntervalMs: number('VIDEO_INTERVAL_MS', 1200),
  videoMaxFuture: number('VIDEO_MAX_FUTURE', 10),
  maxRetries: number('MAX_RETRIES', 5),
  parallelLimit: Math.max(1, number('PARALLEL_LIMIT', 4)),
  autoClaim: flag('AUTO_CLAIM', true),
  userAgent:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 ' +
    'Electron/37.6.0 Safari/537.36',
  androidUserAgent: 'Discord-Android/316011;RNA',
};

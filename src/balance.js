import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const MAX_HISTORY = 200;

function nowIso() {
  return new Date().toISOString();
}

export class BalanceStore {
  constructor(file) {
    this.file = file;
    this.data = { totalOrbs: 0, missionsPaid: 0, history: [] };
    this.load();
  }

  load() {
    try {
      const parsed = JSON.parse(readFileSync(this.file, 'utf8'));
      if (!parsed || typeof parsed !== 'object') return;
      this.data.totalOrbs = Number(parsed.totalOrbs) || 0;
      this.data.missionsPaid = Number(parsed.missionsPaid) || 0;
      this.data.history = Array.isArray(parsed.history)
        ? parsed.history.slice(-MAX_HISTORY)
        : [];
    } catch {
      // primeiro uso ou arquivo invalido: comeca zerado
    }
  }

  save() {
    try {
      mkdirSync(dirname(this.file), { recursive: true });
      this.data.history = this.data.history.slice(-MAX_HISTORY);
      writeFileSync(this.file, JSON.stringify(this.data, null, 2), 'utf8');
    } catch {
      // falha de escrita nao deve derrubar o bot
    }
  }

  get totalOrbs() {
    return this.data.totalOrbs;
  }

  get missionsPaid() {
    return this.data.missionsPaid;
  }

  add(orbs, questName) {
    const amount = Math.max(0, Math.floor(Number(orbs) || 0));
    if (amount <= 0) return;
    this.data.totalOrbs += amount;
    this.data.missionsPaid += 1;
    this.data.history.push({
      at: nowIso(),
      quest: String(questName ?? '').slice(0, 120),
      orbs: amount,
      total: this.data.totalOrbs,
    });
    this.save();
  }

  recent(limit = 10) {
    return this.data.history.slice(-limit).reverse();
  }

  diff(before) {
    return {
      before,
      after: this.totalOrbs,
      earned: this.totalOrbs - before,
    };
  }
}

import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const ALGORITHM = 'aes-256-gcm';

function emptyOwner() {
  return { activeId: null, accounts: {} };
}

function sanitizeProfile(profile = {}) {
  return {
    id: String(profile.id ?? ''),
    username: String(profile.username ?? 'usuario'),
    globalName: profile.globalName ?? profile.global_name ?? null,
    avatar: profile.avatar ?? null,
  };
}

export function displayName(profile) {
  if (!profile) return 'Conta desconectada';
  return profile.globalName || profile.username || 'usuario';
}

export function avatarUrl(profile, size = 128) {
  if (!profile?.id) return null;
  if (profile.avatar) {
    const ext = String(profile.avatar).startsWith('a_') ? 'gif' : 'png';
    return `https://cdn.discordapp.com/avatars/${profile.id}/${profile.avatar}.${ext}?size=${size}`;
  }
  let index = 0;
  try {
    index = Number((BigInt(profile.id) >> 22n) % 6n);
  } catch {
    index = 0;
  }
  return `https://cdn.discordapp.com/embed/avatars/${index}.png`;
}

export class AccountStore {
  constructor({ file, keyFile }) {
    this.file = file;
    this.keyFile = keyFile;
    this.data = { owners: {} };
    this.key = this.loadKey();
    this.load();
  }

  loadKey() {
    try {
      mkdirSync(dirname(this.keyFile), { recursive: true });
    } catch {
      // diretorio ja existe ou sem permissao
    }
    try {
      const existing = readFileSync(this.keyFile);
      if (existing.length === 32) return existing;
    } catch {
      // primeira execucao
    }
    const generated = randomBytes(32);
    try {
      writeFileSync(this.keyFile, generated, { mode: 0o600 });
    } catch {
      // se nao puder gravar, mantem a chave apenas em memoria
    }
    return generated;
  }

  load() {
    try {
      const parsed = JSON.parse(readFileSync(this.file, 'utf8'));
      if (parsed && typeof parsed === 'object' && parsed.owners) {
        this.data = { owners: parsed.owners };
      }
    } catch {
      this.data = { owners: {} };
    }
  }

  save() {
    try {
      mkdirSync(dirname(this.file), { recursive: true });
      writeFileSync(this.file, JSON.stringify(this.data, null, 2), { mode: 0o600 });
    } catch (error) {
      console.error('[contas] falha ao salvar:', error.message);
    }
  }

  encrypt(text) {
    const iv = randomBytes(12);
    const cipher = createCipheriv(ALGORITHM, this.key, iv);
    const encrypted = Buffer.concat([cipher.update(String(text), 'utf8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    return `${iv.toString('base64url')}.${tag.toString('base64url')}.${encrypted.toString('base64url')}`;
  }

  decrypt(payload) {
    try {
      const [ivPart, tagPart, dataPart] = String(payload).split('.');
      if (!ivPart || !tagPart || !dataPart) return null;
      const decipher = createDecipheriv(ALGORITHM, this.key, Buffer.from(ivPart, 'base64url'));
      decipher.setAuthTag(Buffer.from(tagPart, 'base64url'));
      const decrypted = Buffer.concat([
        decipher.update(Buffer.from(dataPart, 'base64url')),
        decipher.final(),
      ]);
      return decrypted.toString('utf8');
    } catch {
      return null;
    }
  }

  owner(ownerId) {
    if (!this.data.owners[ownerId]) this.data.owners[ownerId] = emptyOwner();
    return this.data.owners[ownerId];
  }

  owners() {
    return Object.keys(this.data.owners);
  }

  list(ownerId) {
    const owner = this.owner(ownerId);
    return Object.values(owner.accounts)
      .map((record) => ({
        id: record.id,
        username: record.username,
        globalName: record.globalName ?? null,
        avatar: record.avatar ?? null,
        savedAt: record.savedAt,
        lastUsedAt: record.lastUsedAt,
        active: record.id === owner.activeId,
      }))
      .sort((a, b) => String(b.lastUsedAt).localeCompare(String(a.lastUsedAt)));
  }

  activeInfo(ownerId) {
    const owner = this.owner(ownerId);
    if (!owner.activeId) return null;
    const record = owner.accounts[owner.activeId];
    if (!record) return null;
    return {
      id: record.id,
      username: record.username,
      globalName: record.globalName ?? null,
      avatar: record.avatar ?? null,
    };
  }

  active(ownerId) {
    const owner = this.owner(ownerId);
    if (!owner.activeId) return null;
    return this.get(ownerId, owner.activeId);
  }

  get(ownerId, accountId) {
    const owner = this.owner(ownerId);
    const record = owner.accounts[accountId];
    if (!record) return null;
    const token = this.decrypt(record.token);
    if (!token) return null;
    return {
      id: record.id,
      username: record.username,
      globalName: record.globalName ?? null,
      avatar: record.avatar ?? null,
      savedAt: record.savedAt,
      lastUsedAt: record.lastUsedAt,
      token,
    };
  }

  upsert(ownerId, profile, token, makeActive = true) {
    const owner = this.owner(ownerId);
    const safe = sanitizeProfile(profile);
    if (!safe.id || !token) return null;
    const now = new Date().toISOString();
    const previous = owner.accounts[safe.id];
    owner.accounts[safe.id] = {
      ...safe,
      token: this.encrypt(token),
      savedAt: previous?.savedAt ?? now,
      lastUsedAt: now,
    };
    if (makeActive || !owner.activeId) owner.activeId = safe.id;
    this.save();
    return { ...safe, active: owner.activeId === safe.id };
  }

  touch(ownerId, accountId, profile = null) {
    const owner = this.owner(ownerId);
    const record = owner.accounts[accountId];
    if (!record) return;
    const safe = sanitizeProfile({ ...record, ...(profile ?? {}) });
    record.username = safe.username;
    record.globalName = safe.globalName;
    record.avatar = safe.avatar;
    record.lastUsedAt = new Date().toISOString();
    owner.activeId = accountId;
    this.save();
  }

  setActive(ownerId, accountId) {
    const owner = this.owner(ownerId);
    if (!owner.accounts[accountId]) return false;
    owner.activeId = accountId;
    this.touch(ownerId, accountId);
    return true;
  }

  remove(ownerId, accountId) {
    const owner = this.owner(ownerId);
    if (!owner.accounts[accountId]) return false;
    delete owner.accounts[accountId];
    if (owner.activeId === accountId) {
      const remaining = Object.keys(owner.accounts);
      owner.activeId = remaining[0] ?? null;
    }
    this.save();
    return true;
  }

  removeOwner(ownerId) {
    delete this.data.owners[ownerId];
    this.save();
  }

  count(ownerId) {
    return Object.keys(this.owner(ownerId).accounts).length;
  }
}

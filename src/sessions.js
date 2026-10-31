export class SessionStore {
  constructor() {
    this.sessions = new Map();
  }

  create(userId, data) {
    const session = {
      userId,
      accountId: data.accountId ?? data.profile?.id ?? null,
      profile: data.profile ?? null,
      token: data.token,
      api: data.api,
      manager: data.manager,
      busy: false,
      cancelRequested: false,
      task: null,
      connectedAt: Date.now(),
    };
    this.sessions.set(userId, session);
    return session;
  }

  replace(userId, data) {
    this.cancel(userId);
    return this.create(userId, data);
  }

  get(userId) {
    return this.sessions.get(userId) ?? null;
  }

  has(userId) {
    return this.sessions.has(userId);
  }

  cancel(userId) {
    const session = this.sessions.get(userId);
    if (!session) return false;
    session.cancelRequested = true;
    return true;
  }

  remove(userId) {
    const session = this.sessions.get(userId);
    if (session) {
      session.cancelRequested = true;
      session.token = null;
    }
    this.sessions.delete(userId);
    return session ?? null;
  }

  count() {
    return this.sessions.size;
  }

  entries() {
    return [...this.sessions.values()];
  }
}

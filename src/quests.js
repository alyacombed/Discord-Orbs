import { config } from './config.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const randomBetween = (min, max) => min + Math.random() * (max - min);

export const SUPPORTED_TASKS = [
  'WATCH_VIDEO',
  'WATCH_VIDEO_ON_MOBILE',
  'PLAY_ON_DESKTOP',
  'PLAY_ON_DESKTOP_V2',
  'PLAY_ON_XBOX',
  'PLAY_ON_PLAYSTATION',
  'PLAY_ACTIVITY',
  'ACHIEVEMENT_IN_ACTIVITY',
  'STREAM_ON_DESKTOP',
];

const LABELS = {
  WATCH_VIDEO: 'Video',
  WATCH_VIDEO_ON_MOBILE: 'Video (mobile)',
  PLAY_ON_DESKTOP: 'Jogar no desktop',
  PLAY_ON_DESKTOP_V2: 'Jogar no desktop',
  PLAY_ON_XBOX: 'Jogar no Xbox',
  PLAY_ON_PLAYSTATION: 'Jogar no PlayStation',
  PLAY_ACTIVITY: 'Atividade',
  ACHIEVEMENT_IN_ACTIVITY: 'Conquista',
  STREAM_ON_DESKTOP: 'Stream',
};

export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function progressBar(percent, length = 16) {
  const value = Math.max(0, Math.min(100, Math.floor(percent) || 0));
  const filled = Math.round((value / 100) * length);
  return `${'█'.repeat(filled)}${'░'.repeat(length - filled)} ${value}%`;
}

function parseDate(value) {
  if (!value) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export class Quest {
  constructor(raw) {
    this.raw = raw ?? {};
    this.status = raw?.user_status ?? {};
  }

  updateStatus(status) {
    if (status && typeof status === 'object' && Object.keys(status).length > 0) {
      this.status = status;
    }
  }

  get id() {
    return this.raw.id ?? '';
  }

  get config() {
    return this.raw.config ?? {};
  }

  get application() {
    return this.config.application ?? {};
  }

  get applicationId() {
    return this.application.id ?? '';
  }

  get applicationName() {
    return this.application.name ?? 'Jogo';
  }

  get messages() {
    return this.config.messages ?? {};
  }

  get name() {
    return this.messages.quest_name ?? 'Missao sem nome';
  }

  get rewards() {
    return this.config.rewards_config ?? {};
  }

  get rewardAmount() {
    return Math.max(0, Math.floor(Number(this.rewards.amount) || 0));
  }

  get heroImage() {
    return this.config.assets?.hero ?? '';
  }

  get taskConfig() {
    const version = Number(this.config.config_version) || 2;
    const source = version === 1 ? this.config.task_config : this.config.task_config_v2;
    return source?.tasks ?? {};
  }

  get taskType() {
    const tasks = this.taskConfig;
    for (const key of SUPPORTED_TASKS) {
      if (tasks[key]) return key;
    }
    return Object.keys(tasks).find((key) => tasks[key]) ?? null;
  }

  get taskLabel() {
    return LABELS[this.taskType] ?? this.taskType ?? 'Desconhecido';
  }

  get taskTarget() {
    const type = this.taskType;
    if (!type) return 0;
    return Math.max(0, Math.floor(Number(this.taskConfig[type]?.target) || 0));
  }

  progressValue(type = this.taskType) {
    if (!type) return 0;
    const value = this.status?.progress?.[type]?.value;
    return Math.max(0, Math.floor(Number(value) || 0));
  }

  get percent() {
    const target = this.taskTarget;
    if (target <= 0) return this.isCompleted ? 100 : 0;
    return Math.min(100, Math.floor((this.progressValue() / target) * 100));
  }

  get isCompleted() {
    return Boolean(this.status?.completed_at);
  }

  get isEnrolled() {
    return Boolean(this.status?.enrolled_at);
  }

  get hasClaimed() {
    return Boolean(this.status?.claimed_at);
  }

  get isClaimable() {
    return this.isCompleted && !this.hasClaimed;
  }

  get isPreview() {
    return Boolean(this.raw.preview);
  }

  get startsAt() {
    return parseDate(this.config.starts_at);
  }

  get expiresAt() {
    return parseDate(this.config.expires_at);
  }

  get isExpired() {
    return this.expiresAt !== null && Date.now() > this.expiresAt;
  }

  get isStarted() {
    return this.startsAt === null || Date.now() >= this.startsAt;
  }

  get isValid() {
    return (
      !this.isPreview &&
      !this.isCompleted &&
      !this.isExpired &&
      this.isStarted &&
      Boolean(this.taskType)
    );
  }
}

export class QuestManager {
  constructor(api) {
    this.api = api;
    this.quests = [];
  }

  async fetchQuests() {
    const payload = await this.api.get('/quests/@me');
    this.quests = (payload?.quests ?? []).map((item) => new Quest(item));
    return this.quests;
  }

  find(id) {
    return this.quests.find((quest) => quest.id === id) ?? null;
  }

  valid() {
    return this.quests.filter((quest) => quest.isValid);
  }

  completed() {
    return this.quests.filter((quest) => quest.isCompleted);
  }

  claimable() {
    return this.quests.filter((quest) => quest.isClaimable);
  }

  stats() {
    return {
      total: this.quests.length,
      valid: this.valid().length,
      completed: this.completed().length,
      claimable: this.claimable().length,
    };
  }

  async fetchBalance() {
    try {
      const payload = await this.api.get('/quests/@me');
      if (!payload || typeof payload !== 'object') return null;
      const containers = [payload, payload.user, payload.balance];
      for (const container of containers) {
        if (!container || typeof container !== 'object') continue;
        for (const key of ['orb_balance', 'orbs', 'balance', 'total_orbs', 'orbBalance']) {
          const value = container[key];
          if (Number.isFinite(value) && typeof value !== 'boolean') {
            return Math.floor(value);
          }
        }
      }
    } catch {
      // saldo real indisponivel: usamos o historico local
    }
    return null;
  }

  async accept(quest, android = false) {
    if (quest.isEnrolled) return true;
    const payload = await this.api.post(`/quests/${quest.id}/enroll`, {
      android,
      body: {
        location: android ? 12 : 11,
        is_targeted: false,
        metadata_sealed: null,
        traffic_metadata_raw: quest.raw?.traffic_metadata_raw ?? null,
        traffic_metadata_sealed: quest.raw?.traffic_metadata_sealed ?? null,
      },
    });
    quest.updateStatus(payload);
    return true;
  }

  async claim(quest) {
    if (!quest.isClaimable) return false;
    const payload = await this.api.post(`/quests/${quest.id}/claim-reward`, {
      body: { location: 11, platform: 1 },
    });
    quest.updateStatus(payload);
    return true;
  }

  async process(quest, onProgress) {
    if (quest.isCompleted) {
      if (config.autoClaim) await this.claim(quest);
      return true;
    }

    const android = quest.taskType === 'WATCH_VIDEO_ON_MOBILE';
    await this.accept(quest, android);

    const type = quest.taskType;
    let done = false;

    switch (type) {
      case 'WATCH_VIDEO':
      case 'WATCH_VIDEO_ON_MOBILE':
        done = await this.runVideo(quest, onProgress);
        break;
      case 'PLAY_ON_DESKTOP':
      case 'PLAY_ON_DESKTOP_V2':
      case 'PLAY_ON_XBOX':
      case 'PLAY_ON_PLAYSTATION':
        done = await this.runHeartbeat(quest, onProgress, {
          payload: { application_id: quest.applicationId },
          delayMin: 20,
          delayMax: 70,
        });
        break;
      case 'PLAY_ACTIVITY':
        done = await this.runHeartbeat(quest, onProgress, {
          payload: { stream_key: `call:${quest.id}:1` },
          delayMin: 18,
          delayMax: 55,
        });
        break;
      case 'STREAM_ON_DESKTOP':
        done = await this.runHeartbeat(quest, onProgress, {
          payload: { stream_key: `guild:0:${quest.id}:${Math.floor(randomBetween(1000, 9999))}` },
          delayMin: 20,
          delayMax: 60,
        });
        break;
      case 'ACHIEVEMENT_IN_ACTIVITY':
        done = await this.runAchievement(quest, onProgress);
        break;
      default:
        return false;
    }

    if (done && config.autoClaim) {
      await sleep(randomBetween(800, 1600));
      try {
        await this.claim(quest);
      } catch {
        // resgate manual pode ser necessario (captcha)
      }
    }
    return done;
  }

  async runVideo(quest, onProgress) {
    const type = quest.taskType;
    const target = quest.taskTarget;
    let done = quest.progressValue(type);
    const enrolledAt = parseDate(quest.status?.enrolled_at) ?? Date.now();

    for (let attempt = 0; attempt < 900 && done < target; attempt += 1) {
      const allowed = Math.floor((Date.now() - enrolledAt) / 1000) + config.videoMaxFuture;
      const diff = allowed - done;
      if (diff < config.videoSpeed) {
        await sleep((config.videoSpeed - diff + 1) * 1000);
        continue;
      }

      const timestamp = Math.min(target, done + config.videoSpeed);
      const payload = await this.api.post(`/quests/${quest.id}/video-progress`, {
        body: { timestamp },
      });
      quest.updateStatus(payload);

      const next = quest.progressValue(type);
      if (next > done) done = next;
      if (payload?.completed_at) return true;

      await onProgress?.({
        title: `Video: ${quest.name}`,
        percent: target > 0 ? Math.min(100, Math.floor((done / target) * 100)) : 0,
        text: `${formatDuration(done)} / ${formatDuration(target)}`,
      });
      await sleep(config.videoIntervalMs + randomBetween(200, 900));
    }

    if (done >= target) {
      try {
        const payload = await this.api.post(`/quests/${quest.id}/video-progress`, {
          body: { timestamp: target },
        });
        quest.updateStatus(payload);
        if (payload?.completed_at) return true;
      } catch {
        // ignora: status final sera checado abaixo
      }
    }
    return quest.isCompleted;
  }

  async runHeartbeat(quest, onProgress, options) {
    const type = quest.taskType;
    const target = quest.taskTarget;
    let done = quest.progressValue(type);

    for (let beat = 0; beat < 500 && !quest.isCompleted; beat += 1) {
      await sleep(randomBetween(options.delayMin, options.delayMax) * 1000);
      const payload = await this.api.post(`/quests/${quest.id}/heartbeat`, {
        body: { ...options.payload, terminal: false },
      });
      quest.updateStatus(payload);
      if (payload?.completed_at) return true;

      const next = quest.progressValue(type);
      if (next > done) {
        done = next;
        await onProgress?.({
          title: `${quest.taskLabel}: ${quest.name}`,
          percent: target > 0 ? Math.min(100, Math.floor((done / target) * 100)) : 0,
          text: `${formatDuration(done)} / ${formatDuration(target)}`,
        });
      }
    }

    try {
      const payload = await this.api.post(`/quests/${quest.id}/heartbeat`, {
        body: { ...options.payload, terminal: true },
      });
      quest.updateStatus(payload);
    } catch {
      // encerramento best-effort
    }
    return quest.isCompleted;
  }

  async runAchievement(quest, onProgress) {
    const appId = quest.applicationId;
    if (!appId) return false;

    await onProgress?.({
      title: `Conquista: ${quest.name}`,
      percent: 10,
      text: 'Autorizando aplicacao...',
    });

    const authorize = await this.api.post('/oauth2/authorize', {
      query: {
        response_type: 'code',
        client_id: appId,
        scope: 'identify applications.commands applications.entitlements',
        state: '',
      },
      body: {
        permissions: '0',
        authorize: true,
        integration_type: 1,
        location_context: { guild_id: '10000', channel_id: '10000', channel_type: 10000 },
      },
    });

    const code = /code=([^&]+)/.exec(authorize?.location ?? '')?.[1];
    if (!code) return false;

    const ticket = await this.api.post(`/applications/${appId}/proxy-tickets`, { body: {} });
    if (!ticket?.ticket) return false;

    const referer =
      `https://${appId}.discordsays.com/?instance_id=example-cl-instance` +
      `&platform=desktop&discord_proxy_ticket=${ticket.ticket}`;
    const headers = {
      'Content-Type': 'application/json',
      'X-Auth-Token': '',
      'X-Discord-Quest-ID': quest.id,
      Referer: referer,
      'User-Agent': config.userAgent,
    };

    const authResponse = await fetch(`https://${appId}.discordsays.com/.proxy/acf/authorize`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ code }),
    });
    const authData = await authResponse.json().catch(() => ({}));
    if (!authData?.token) return false;
    headers['X-Auth-Token'] = authData.token;

    await onProgress?.({
      title: `Conquista: ${quest.name}`,
      percent: 60,
      text: 'Enviando progresso...',
    });

    const target = Math.max(1, Number(quest.taskConfig?.ACHIEVEMENT_IN_ACTIVITY?.target) || 1);
    const progressResponse = await fetch(
      `https://${appId}.discordsays.com/.proxy/acf/quest/progress`,
      { method: 'POST', headers, body: JSON.stringify({ progress: target }) },
    );

    try {
      const tokens = await this.api.get('/oauth2/tokens');
      const token = Array.isArray(tokens)
        ? tokens.find((item) => item?.application?.id === appId)
        : null;
      if (token?.id) await this.api.delete(`/oauth2/tokens/${token.id}`);
    } catch {
      // revogacao opcional
    }

    return progressResponse.ok;
  }

  async processAll(quests, onProgress, limit = config.parallelLimit) {
    const queue = [...quests];
    const results = { success: 0, failed: 0, total: quests.length };
    const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
      while (queue.length > 0) {
        const quest = queue.shift();
        try {
          const ok = await this.process(quest, onProgress);
          if (ok) results.success += 1;
          else results.failed += 1;
        } catch {
          results.failed += 1;
        }
        await sleep(randomBetween(1000, 3000));
      }
    });
    await Promise.all(workers);
    return results;
  }
}

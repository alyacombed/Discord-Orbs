import {
  ActionRowBuilder,
  ActivityType,
  ButtonBuilder,
  ButtonStyle,
  Client,
  Events,
  GatewayIntentBits,
  MessageFlags,
  ModalBuilder,
  SlashCommandBuilder,
  TextInputBuilder,
  TextInputStyle,
} from 'discord.js';

import { config } from './config.js';
import { AccountStore, displayName } from './accounts.js';
import { BalanceStore } from './balance.js';
import { DiscordApi, TokenExpiredError } from './api.js';
import { QuestManager } from './quests.js';
import { SessionStore } from './sessions.js';
import {
  COLORS,
  accountComponents,
  accountRemoveRow,
  accountsEmbed,
  balanceEmbed,
  embed,
  noticeEmbed,
  panelComponents,
  panelEmbed,
  progressEmbed,
  progressOverviewEmbed,
  questSelectEmbed,
  questSelectRow,
  questSummaryEmbed,
  statusEmbed,
  tutorialComponents,
  tutorialEmbed,
  welcomeComponents,
  welcomeEmbed,
} from './ui.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const balance = new BalanceStore(config.balanceFile);
const sessions = new SessionStore();
const accounts = new AccountStore({
  file: config.accountsFile,
  keyFile: config.accountsKeyFile,
});

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

async function safeError(interaction, error) {
  const message =
    error instanceof TokenExpiredError
      ? 'Sua conta expirou ou o token é inválido. Conecte a conta novamente.'
      : `Erro: ${String(error?.message ?? error).slice(0, 250)}`;
  const payload = {
    embeds: [noticeEmbed('❌ Algo deu errado', message, 'danger')],
    flags: MessageFlags.Ephemeral,
  };
  try {
    if (interaction.deferred || interaction.replied) await interaction.followUp(payload);
    else await interaction.reply(payload);
  } catch {
    // sem resposta possível
  }
}

async function busyNotice(interaction) {
  const payload = {
    embeds: [
      noticeEmbed(
        '⏳ Processamento em andamento',
        'Aguarde a execução atual terminar ou use **Cancelar**.',
        'warning',
      ),
    ],
    flags: MessageFlags.Ephemeral,
  };
  if (interaction.deferred || interaction.replied) await interaction.followUp(payload);
  else await interaction.reply(payload);
}

function loginModal() {
  const input = new TextInputBuilder()
    .setCustomId('token')
    .setLabel('Token de usuário do Discord')
    .setPlaceholder('Cole seu token aqui...')
    .setRequired(true)
    .setMinLength(20)
    .setMaxLength(200)
    .setStyle(TextInputStyle.Paragraph);

  return new ModalBuilder()
    .setCustomId('mo:login_modal')
    .setTitle('Conectar conta do Discord')
    .addComponents(new ActionRowBuilder().addComponents(input));
}

function backRow(extra = []) {
  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId('mo:panel')
      .setLabel('Voltar ao painel')
      .setEmoji('⬅️')
      .setStyle(ButtonStyle.Secondary),
    ...extra,
  );
  return row;
}

class ProgressReporter {
  constructor(user) {
    this.user = user;
    this.message = null;
    this.lastUpdate = 0;
  }

  async progress(state) {
    if (!this.user) return;
    const now = Date.now();
    if (now - this.lastUpdate < 1500) return;
    this.lastUpdate = now;
    const built = progressEmbed(state.title, state.percent, state.text);
    try {
      if (this.message) await this.message.edit({ embeds: [built] });
      else this.message = await this.user.send({ embeds: [built] });
    } catch {
      // DM fechada ou mensagem apagada
    }
  }

  async finish(built) {
    if (!this.user) return;
    try {
      if (this.message) await this.message.edit({ embeds: [built] });
      else await this.user.send({ embeds: [built] });
    } catch {
      try {
        await this.user.send({ embeds: [built] });
      } catch {
        // DM indisponível
      }
    }
  }
}

async function connectAccount(ownerIdValue, token) {
  const api = new DiscordApi(token);
  const me = await api.get('/users/@me');
  const manager = new QuestManager(api);
  await manager.fetchQuests();

  const profile = {
    id: String(me.id ?? ''),
    username: me.username ?? 'usuario',
    globalName: me.global_name ?? null,
    avatar: me.avatar ?? null,
  };

  accounts.upsert(ownerIdValue, profile, token, true);
  const session = sessions.replace(ownerIdValue, {
    accountId: profile.id,
    profile,
    token,
    api,
    manager,
  });

  return { session, me, stats: manager.stats() };
}

async function restoreSessions() {
  const owners = accounts.owners();
  if (owners.length === 0) return;

  for (const owner of owners) {
    const record = accounts.active(owner);
    if (!record) continue;
    try {
      const { session } = await connectAccount(owner, record.token);
      console.log(`[contas] conta restaurada: ${displayName(session.profile)} (${owner})`);
    } catch (error) {
      console.warn(
        `[contas] não foi possível restaurar ${displayName(record)}: ${String(error?.message ?? error)}`,
      );
    }
  }
}

async function collectPanelData(owner) {
  let session = sessions.get(owner);
  let stats = null;
  let apiBalance = null;
  let claimableOrbs = 0;

  if (session) {
    try {
      await session.manager.fetchQuests();
      stats = session.manager.stats();
      apiBalance = await session.manager.fetchBalance();
      claimableOrbs = session.manager
        .claimable()
        .reduce((acc, quest) => acc + quest.rewardAmount, 0);
    } catch (error) {
      if (error instanceof TokenExpiredError) {
        sessions.remove(owner);
      } else {
        console.error('[painel]', error);
      }
    }
  }

  session = sessions.get(owner);

  return { session, stats, apiBalance, claimableOrbs };
}

function panelPayload(owner, session, stats, apiBalance, claimableOrbs) {
  const busy = Boolean(session?.busy);
  return {
    embeds: [
      panelEmbed({
        client,
        account: session?.profile ?? null,
        stats,
        historyOrbs: balance.totalOrbs,
        apiBalance,
        claimableOrbs,
        parallelLimit: config.parallelLimit,
        latency: Math.max(0, Math.round(client.ws.ping)),
        busy,
      }),
    ],
    components: panelComponents({ loggedIn: Boolean(session), busy }),
  };
}

async function openPanel(interaction, { update = false, ephemeral = true } = {}) {
  if (update && interaction.isMessageComponent()) {
    await interaction.deferUpdate().catch(() => {});
    const { session, stats, apiBalance, claimableOrbs } = await collectPanelData(interaction.user.id);
    await interaction
      .editReply(panelPayload(interaction.user.id, session, stats, apiBalance, claimableOrbs))
      .catch(() => {});
    return;
  }

  if (!interaction.deferred && !interaction.replied) {
    if (ephemeral) await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    else await interaction.deferReply();
  }
  const { session, stats, apiBalance, claimableOrbs } = await collectPanelData(interaction.user.id);
  await interaction.editReply(
    panelPayload(interaction.user.id, session, stats, apiBalance, claimableOrbs),
  );
}

function accountsPayload(owner) {
  const session = sessions.get(owner);
  const list = accounts.list(owner);
  const activeId = accounts.owner(owner).activeId;
  const busy = Boolean(session?.busy);
  return {
    embeds: [accountsEmbed({ accounts: list, activeId, busy })],
    components: accountComponents({ accounts: list, loggedIn: Boolean(session), busy }),
  };
}

async function handleLogin(interaction) {
  const session = sessions.get(interaction.user.id);
  if (session?.busy) {
    await busyNotice(interaction);
    return;
  }
  await interaction.showModal(loginModal());
}

async function handleLoginModal(interaction) {
  const token = interaction.fields.getTextInputValue('token').trim();
  if (!token) {
    await interaction.reply({
      embeds: [noticeEmbed('❌ Token vazio', 'Cole um token válido para continuar.', 'danger')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  const current = sessions.get(interaction.user.id);
  if (current?.busy) {
    await interaction.reply({
      embeds: [
        noticeEmbed('⏳ Aguarde', 'Existe um processamento em andamento. Cancele antes de trocar de conta.', 'warning'),
      ],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    const { session, stats, me } = await connectAccount(interaction.user.id, token);
    const built = panelEmbed({
      client,
      account: session.profile,
      stats,
      historyOrbs: balance.totalOrbs,
      apiBalance: await session.manager.fetchBalance(),
      claimableOrbs: 0,
      parallelLimit: config.parallelLimit,
      latency: Math.max(0, Math.round(client.ws.ping)),
      busy: false,
    });
    built.setFooter({
      text: `Conta salva com sucesso • ${me.username ?? displayName(session.profile)}`,
    });
    await interaction.editReply({ embeds: [built], components: panelComponents({ loggedIn: true }) });
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleAccounts(interaction) {
  if (!interaction.deferred && !interaction.replied) {
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  }
  await interaction.editReply(accountsPayload(interaction.user.id));
}

function isEphemeralMessage(interaction) {
  return Boolean(interaction.message?.flags?.has(MessageFlags.Ephemeral));
}

async function handleTutorial(interaction, page = 0) {
  const payload = {
    embeds: [tutorialEmbed(page, client)],
    components: tutorialComponents(page),
  };
  if (isEphemeralMessage(interaction)) {
    await interaction.update(payload).catch(() => {});
  } else {
    await interaction.reply({ ...payload, flags: MessageFlags.Ephemeral });
  }
}

async function handleClose(interaction) {
  const payload = {
    embeds: [
      noticeEmbed('📚 Tutorial fechado', 'Use **/painel** sempre que precisar de ajuda.', 'info'),
    ],
    components: [],
  };
  if (isEphemeralMessage(interaction)) {
    await interaction.update(payload).catch(() => {});
  } else {
    await interaction.reply({ ...payload, flags: MessageFlags.Ephemeral });
  }
}

async function handleAccountSwitch(interaction) {
  const owner = interaction.user.id;
  const accountId = interaction.values[0];
  const session = sessions.get(owner);
  if (session?.busy) {
    await busyNotice(interaction);
    return;
  }

  const record = accounts.get(owner, accountId);
  if (!record) {
    await interaction.reply({
      embeds: [noticeEmbed('❌ Conta indisponível', 'Essa conta não foi encontrada. Tente novamente.', 'danger')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    accounts.setActive(owner, accountId);
    await connectAccount(owner, record.token);
    await interaction.editReply(accountsPayload(owner));
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleAccountRemove(interaction) {
  const list = accounts.list(interaction.user.id);
  if (list.length === 0) {
    await interaction.reply({
      embeds: [noticeEmbed('🗑️ Nenhuma conta', 'Não há contas salvas para remover.', 'warning')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.reply({
    embeds: [
      noticeEmbed(
        '🗑️ Remover conta',
        'Escolha abaixo qual conta deseja remover.\n> Essa ação apaga o token salvo e não pode ser desfeita.',
        'warning',
      ),
    ],
    components: [accountRemoveRow(list)],
    flags: MessageFlags.Ephemeral,
  });
}

async function handleAccountRemoveSelect(interaction) {
  const owner = interaction.user.id;
  const accountId = interaction.values[0];
  const session = sessions.get(owner);
  if (session?.busy) {
    await busyNotice(interaction);
    return;
  }

  const target = accounts.list(owner).find((item) => item.id === accountId);
  if (!target) {
    await interaction.reply({
      embeds: [noticeEmbed('❌ Conta não encontrada', 'Atualize a lista e tente novamente.', 'danger')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    const wasActive = accounts.owner(owner).activeId === accountId;
    accounts.remove(owner, accountId);

    if (wasActive) {
      sessions.remove(owner);
      const next = accounts.active(owner);
      if (next) {
        try {
          await connectAccount(owner, next.token);
        } catch (error) {
          console.warn(`[contas] falha ao ativar conta seguinte: ${String(error?.message ?? error)}`);
        }
      }
    }

    const payload = accountsPayload(owner);
    payload.embeds.unshift(
      noticeEmbed(
        '✅ Conta removida',
        `A conta **${displayName(target)}** foi removida com sucesso.`,
        'success',
      ),
    );
    await interaction.editReply(payload);
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleLogout(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('ℹ️ Você não está conectado', 'Use **Conectar conta** para entrar.', 'info')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  sessions.remove(interaction.user.id);
  await interaction.reply({
    embeds: [
      noticeEmbed(
        '🚪 Desconectado',
        'Sua conta continua salva em **Contas** e pode ser reconectada com um clique.',
        'success',
      ),
    ],
    flags: MessageFlags.Ephemeral,
  });
}

async function handleAuto(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('🔐 Conecte sua conta', 'Use **Conectar conta** no painel primeiro.', 'warning')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  if (session.busy) {
    await busyNotice(interaction);
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  const before = balance.totalOrbs;
  session.busy = true;
  session.cancelRequested = false;

  session.task = runAuto(interaction, session, before).finally(() => {
    session.busy = false;
    session.cancelRequested = false;
    session.task = null;
  });
}

async function runAuto(interaction, session, before) {
  const user = await client.users.fetch(session.userId).catch(() => null);
  const reporter = new ProgressReporter(user);

  try {
    await session.manager.fetchQuests();
    const quests = session.manager.valid();

    await interaction
      .editReply({
        embeds: [
          embed(
            '🚀 Auto-Quest iniciado',
            [
              `**${quests.length}** quest(s) na fila.`,
              `💎 Saldo antes: \`${before}\``,
              '',
              '> Os resultados serão enviados por DM.',
            ].join('\n'),
            COLORS.brand,
          ),
        ],
      })
      .catch(() => {});

    let success = 0;
    let failed = 0;

    for (let index = 0; index < quests.length; index += 1) {
      if (session.cancelRequested) break;
      const quest = quests[index];

      try {
        const ok = await session.manager.process(quest, (state) => reporter.progress(state));
        if (ok) {
          success += 1;
          if (quest.hasClaimed && quest.rewardAmount > 0) {
            balance.add(quest.rewardAmount, quest.name);
          }
        } else {
          failed += 1;
        }

        if (user) {
          await user
            .send({ embeds: [questSummaryEmbed(quest, index + 1, quests.length, ok)] })
            .catch(() => {});
        }
      } catch (error) {
        failed += 1;
        if (user) {
          await user
            .send({
              embeds: [
                noticeEmbed(
                  '⚠️ Quest interrompida',
                  `**${quest.name}**\n\`${String(error?.message ?? error).slice(0, 150)}\``,
                  'danger',
                ),
              ],
            })
            .catch(() => {});
        }
        if (error instanceof TokenExpiredError) break;
      }

      if (index < quests.length - 1) await sleep(1500);
    }

    const diff = balance.diff(before);
    const summary = embed(
      '🏁 Auto-Quest finalizado',
      [
        `✅ Concluídas: \`${success}\``,
        `⚠️ Falhas: \`${failed}\``,
        `🎯 Total: \`${quests.length}\``,
        '',
        `💎 Saldo antes: \`${diff.before}\``,
        `💎 Saldo depois: \`${diff.after}\``,
        `✨ Ganhos na sessão: \`${diff.earned}\``,
      ].join('\n'),
      COLORS.success,
    );

    await reporter.finish(summary);
    await interaction.followUp({ embeds: [summary], flags: MessageFlags.Ephemeral }).catch(() => {});
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleSelectQuest(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('🔐 Conecte sua conta', 'Use **Conectar conta** no painel primeiro.', 'warning')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  if (session.busy) {
    await busyNotice(interaction);
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  try {
    await session.manager.fetchQuests();
    const quests = session.manager.valid();
    if (quests.length === 0) {
      await interaction.editReply({
        embeds: [noticeEmbed('🎉 Tudo em dia', 'Nenhuma quest válida disponível no momento.', 'success')],
        components: [backRow()],
      });
      return;
    }
    await interaction.editReply({
      embeds: [questSelectEmbed(quests)],
      components: [questSelectRow(quests), backRow()],
    });
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleChoose(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('🔐 Você não está conectado', 'Conecte sua conta primeiro.', 'danger')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  if (session.busy) {
    await busyNotice(interaction);
    return;
  }

  const quest = session.manager.find(interaction.values[0]);
  if (!quest || !quest.isValid) {
    await interaction.reply({
      embeds: [noticeEmbed('❌ Quest indisponível', 'Atualize a lista e tente novamente.', 'danger')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  const before = balance.totalOrbs;
  await interaction.reply({
    embeds: [progressEmbed(`Processando: ${quest.name}`, 0, 'Aguarde...')],
    flags: MessageFlags.Ephemeral,
  });

  session.busy = true;
  session.cancelRequested = false;
  session.task = runManual(interaction, session, quest, before).finally(() => {
    session.busy = false;
    session.cancelRequested = false;
    session.task = null;
  });
}

async function runManual(interaction, session, quest, before) {
  let lastEdit = 0;
  const update = async (state, force = false) => {
    const now = Date.now();
    if (!force && now - lastEdit < 1200) return;
    lastEdit = now;
    await interaction
      .editReply({ embeds: [progressEmbed(state.title, state.percent, state.text)] })
      .catch(() => {});
  };

  try {
    const ok = await session.manager.process(quest, update);
    if (ok && quest.hasClaimed && quest.rewardAmount > 0) {
      balance.add(quest.rewardAmount, quest.name);
    }
    const diff = balance.diff(before);
    const built = embed(
      ok ? '✅ Quest concluída' : '⚠️ Quest não concluída',
      [
        `### ${quest.name}`,
        `**Tipo:** \`${quest.taskType ?? 'desconhecido'}\``,
        '',
        `💎 Saldo antes: \`${diff.before}\``,
        `💎 Saldo depois: \`${diff.after}\``,
        `✨ Ganhos: \`${diff.earned}\``,
      ].join('\n'),
      ok ? COLORS.success : COLORS.danger,
    );
    await interaction.editReply({ embeds: [built], components: [backRow()] }).catch(() => {});
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleByte(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('🔐 Conecte sua conta', 'Use **Conectar conta** no painel primeiro.', 'warning')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  if (session.busy) {
    await busyNotice(interaction);
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  const before = balance.totalOrbs;
  session.busy = true;
  session.cancelRequested = false;
  session.task = runByte(interaction, session, before).finally(() => {
    session.busy = false;
    session.cancelRequested = false;
    session.task = null;
  });
}

async function runByte(interaction, session, before) {
  const user = await client.users.fetch(session.userId).catch(() => null);
  const reporter = new ProgressReporter(user);

  try {
    await session.manager.fetchQuests();
    const quests = session.manager.valid();
    await interaction
      .editReply({
        embeds: [
          embed(
            '⚡ Turbo iniciado',
            `Processando **${quests.length}** quest(s) em paralelo (limite \`${config.parallelLimit}\`).\n💎 Saldo antes: \`${before}\``,
            COLORS.accent,
          ),
        ],
      })
      .catch(() => {});

    const results = await session.manager.processAll(
      quests,
      (state) => reporter.progress(state),
      config.parallelLimit,
    );

    for (const quest of quests) {
      if (quest.hasClaimed && quest.rewardAmount > 0) {
        balance.add(quest.rewardAmount, quest.name);
      }
    }

    const diff = balance.diff(before);
    const summary = embed(
      '🏁 Turbo finalizado',
      [
        `✅ Concluídas: \`${results.success}\``,
        `⚠️ Falhas: \`${results.failed}\``,
        `🎯 Total: \`${results.total}\``,
        '',
        `💎 Saldo antes: \`${diff.before}\``,
        `💎 Saldo depois: \`${diff.after}\``,
        `✨ Ganhos na sessão: \`${diff.earned}\``,
      ].join('\n'),
      COLORS.success,
    );

    await reporter.finish(summary);
    await interaction.editReply({ embeds: [summary], components: [backRow()] }).catch(() => {});
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleCancel(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session || !session.busy) {
    await interaction.reply({
      embeds: [noticeEmbed('ℹ️ Nada para cancelar', 'Nenhum processamento em andamento.', 'info')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  session.cancelRequested = true;
  await interaction.reply({
    embeds: [
      noticeEmbed(
        '🛑 Cancelamento solicitado',
        'O processamento será interrompido após a quest atual. A quest em andamento termina sozinha.',
        'warning',
      ),
    ],
    flags: MessageFlags.Ephemeral,
  });
}

async function handleProgress(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!session) {
    await interaction.reply({
      embeds: [noticeEmbed('🔐 Conecte sua conta', 'Use **Conectar conta** no painel primeiro.', 'warning')],
      flags: MessageFlags.Ephemeral,
    });
    return;
  }
  await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  try {
    await session.manager.fetchQuests();
    const valid = session.manager.valid();
    const stats = session.manager.stats();
    await interaction.editReply({
      embeds: [progressOverviewEmbed(stats, valid)],
      components: [backRow()],
    });
  } catch (error) {
    await safeError(interaction, error);
  }
}

async function handleStatusBot(interaction) {
  const session = sessions.get(interaction.user.id);
  const stats = session?.manager.stats() ?? null;
  await interaction.reply({
    embeds: [
      statusEmbed({
        sessions: sessions.count(),
        parallelLimit: config.parallelLimit,
        totalOrbs: balance.totalOrbs,
        stats,
      }),
    ],
    flags: MessageFlags.Ephemeral,
  });
}

async function handleBalance(interaction) {
  const session = sessions.get(interaction.user.id);
  if (!interaction.deferred && !interaction.replied) {
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
  }

  let apiBalance = null;
  let claimableOrbs = 0;
  let pending = 0;
  if (session) {
    try {
      await session.manager.fetchQuests();
      apiBalance = await session.manager.fetchBalance();
      claimableOrbs = session.manager
        .claimable()
        .reduce((acc, quest) => acc + quest.rewardAmount, 0);
      pending = session.manager.valid().length;
    } catch (error) {
      await safeError(interaction, error);
      return;
    }
  }

  await interaction.editReply({
    embeds: [balanceEmbed(balance, { apiBalance, claimableOrbs, pending })],
    components: [backRow()],
  });
}

async function handleButton(interaction) {
  if (interaction.customId.startsWith('mo:tut:')) {
    const pagePart = interaction.customId.slice('mo:tut:'.length);
    await handleTutorial(interaction, Number(pagePart) || 0);
    return;
  }

  const action = interaction.customId.slice('mo:'.length);
  const handlers = {
    login: handleLogin,
    auto: handleAuto,
    select: handleSelectQuest,
    progress: handleProgress,
    balance: handleBalance,
    cancel: handleCancel,
    byte: handleByte,
    logout: handleLogout,
    accounts: handleAccounts,
    account_remove: handleAccountRemove,
    tutorial: (buttonInteraction) => handleTutorial(buttonInteraction, 0),
    close: handleClose,
    open: (buttonInteraction) => openPanel(buttonInteraction),
    refresh: (buttonInteraction) => openPanel(buttonInteraction, { update: true }),
    panel: (buttonInteraction) => openPanel(buttonInteraction, { update: true }),
  };
  const handler = handlers[action];
  if (handler) await handler(interaction);
}

async function handleCommand(interaction) {
  switch (interaction.commandName) {
    case 'painel':
      await interaction.reply({
        embeds: [welcomeEmbed(client)],
        components: welcomeComponents(),
      });
      return;
    case 'saldo':
      await handleBalance(interaction);
      return;
    case 'contas':
      await handleAccounts(interaction);
      return;
    case 'statusbot':
      await handleStatusBot(interaction);
      return;
    default:
  }
}

const commands = [
  new SlashCommandBuilder()
    .setName('painel')
    .setDescription('Abre o painel principal do Monsters Orbs.'),
  new SlashCommandBuilder()
    .setName('saldo')
    .setDescription('Mostra seu saldo de Orbs e o histórico de ganhos.'),
  new SlashCommandBuilder()
    .setName('contas')
    .setDescription('Gerencia suas contas do Discord salvas.'),
  new SlashCommandBuilder()
    .setName('statusbot')
    .setDescription('Mostra o status do bot.'),
];

async function registerCommands() {
  try {
    if (config.guildId) {
      const guild = await client.guilds.fetch(config.guildId);
      await guild.commands.set(commands);
    } else {
      await client.application.commands.set(commands);
    }
  } catch (error) {
    console.error('[comandos] falha ao registrar:', error.message);
  }
}

client.once(Events.ClientReady, async (ready) => {
  console.log('[acesso] Bot aberto para todos os usuários.');

  await restoreSessions();
  await registerCommands();
  ready.user.setActivity('suas quests ✨', { type: ActivityType.Watching });
  console.log(`[online] ${ready.user.tag}`);
});

client.on(Events.InteractionCreate, async (interaction) => {
  try {
    if (interaction.isChatInputCommand()) {
      await handleCommand(interaction);
      return;
    }
    if (interaction.isModalSubmit() && interaction.customId === 'mo:login_modal') {
      await handleLoginModal(interaction);
      return;
    }
    if (interaction.isStringSelectMenu()) {
      if (interaction.customId === 'mo:choose') {
        await handleChoose(interaction);
        return;
      }
      if (interaction.customId === 'mo:account_switch') {
        await handleAccountSwitch(interaction);
        return;
      }
      if (interaction.customId === 'mo:account_remove_select') {
        await handleAccountRemoveSelect(interaction);
        return;
      }
    }
    if (interaction.isButton() && interaction.customId.startsWith('mo:')) {
      await handleButton(interaction);
    }
  } catch (error) {
    console.error('[interacao]', error);
    await safeError(interaction, error);
  }
});

process.on('unhandledRejection', (error) => {
  console.error('[unhandledRejection]', error);
});
process.on('uncaughtException', (error) => {
  console.error('[uncaughtException]', error);
});

if (!config.botToken) {
  console.error('ERRO: BOT_TOKEN não configurado. Copie .env.example para .env e preencha.');
  process.exit(1);
}

client.login(config.botToken).catch((error) => {
  console.error('[login] falha:', error.message);
  process.exit(1);
});

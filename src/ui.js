import {
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  EmbedBuilder,
  StringSelectMenuBuilder,
} from 'discord.js';
import { avatarUrl, displayName } from './accounts.js';
import { formatDuration, progressBar } from './quests.js';

export const COLORS = {
  brand: 0x8b5cf6,
  accent: 0x22d3ee,
  info: 0x5865f2,
  success: 0x22c55e,
  warning: 0xf59e0b,
  danger: 0xef4444,
  orb: 0xfacc15,
  neutral: 0x111827,
};

const TASK_EMOJI = {
  WATCH_VIDEO: '🎬',
  WATCH_VIDEO_ON_MOBILE: '📱',
  PLAY_ON_DESKTOP: '🎮',
  PLAY_ON_DESKTOP_V2: '🎮',
  PLAY_ON_XBOX: '🎮',
  PLAY_ON_PLAYSTATION: '🎮',
  PLAY_ACTIVITY: '🕹️',
  ACHIEVEMENT_IN_ACTIVITY: '🏆',
  STREAM_ON_DESKTOP: '📡',
};

function taskEmoji(type) {
  return TASK_EMOJI[type] ?? '✨';
}

export function embed(title, description, color = COLORS.brand) {
  return new EmbedBuilder()
    .setTitle(title)
    .setDescription(description)
    .setColor(color)
    .setTimestamp(new Date());
}

export function noticeEmbed(title, description, tone = 'info') {
  const palette = {
    info: COLORS.info,
    success: COLORS.success,
    warning: COLORS.warning,
    danger: COLORS.danger,
  };
  return embed(title, description, palette[tone] ?? COLORS.brand);
}

export function progressEmbed(title, percent, text) {
  const built = embed(title, `${progressBar(percent)}\n${text ?? ''}`, COLORS.warning)
    .setFooter({ text: 'Monsters Orbs • processando...' });
  return built;
}

function button(customId, label, style = ButtonStyle.Secondary, options = {}) {
  const built = new ButtonBuilder().setCustomId(customId).setLabel(label).setStyle(style);
  if (options.emoji) built.setEmoji(options.emoji);
  if (options.disabled) built.setDisabled(true);
  return built;
}

export function panelComponents({ loggedIn = false, busy = false } = {}) {
  const rowOne = new ActionRowBuilder().addComponents(
    button('mo:auto', 'Auto-Quest', ButtonStyle.Success, {
      emoji: '🚀',
      disabled: !loggedIn || busy,
    }),
    button('mo:select', 'Escolher quest', ButtonStyle.Primary, {
      emoji: '🎯',
      disabled: !loggedIn || busy,
    }),
    button('mo:byte', 'Turbo', ButtonStyle.Secondary, {
      emoji: '⚡',
      disabled: !loggedIn || busy,
    }),
  );

  const rowTwo = new ActionRowBuilder().addComponents(
    button('mo:refresh', 'Atualizar', ButtonStyle.Secondary, { emoji: '🔄' }),
    button('mo:balance', 'Saldo', ButtonStyle.Secondary, { emoji: '💎' }),
    button('mo:progress', 'Progresso', ButtonStyle.Secondary, {
      emoji: '📊',
      disabled: !loggedIn,
    }),
    button('mo:accounts', 'Contas', ButtonStyle.Secondary, { emoji: '👤' }),
  );

  const rowThree = new ActionRowBuilder().addComponents(
    button('mo:cancel', 'Cancelar', ButtonStyle.Danger, {
      emoji: '🛑',
      disabled: !busy,
    }),
    button('mo:login', 'Conectar conta', ButtonStyle.Primary, { emoji: '➕' }),
    button('mo:tutorial', 'Tutorial', ButtonStyle.Secondary, { emoji: '📚' }),
    button('mo:logout', 'Desconectar', ButtonStyle.Danger, {
      emoji: '🚪',
      disabled: !loggedIn,
    }),
  );

  return [rowOne, rowTwo, rowThree];
}

export function panelEmbed({
  client,
  account,
  stats = null,
  historyOrbs = 0,
  apiBalance = null,
  claimableOrbs = 0,
  parallelLimit = 1,
  latency = 0,
  busy = false,
} = {}) {
  const connected = Boolean(account);
  const name = connected ? displayName(account) : 'Nenhuma conta conectada';

  const description = [
    '### ✨ Monsters Orbs',
    '> Automatize suas quests, resgate Orbs e acompanhe tudo em um só lugar.',
    '',
    connected
      ? `**Conta ativa:** ${name}`
      : 'Use **Conectar conta** para salvar sua conta e liberar o painel.',
  ].join('\n');

  const built = new EmbedBuilder()
    .setDescription(description)
    .setColor(connected ? COLORS.brand : COLORS.neutral)
    .setTimestamp(new Date())
    .setFooter({
      text: `Monsters Orbs • ${busy ? 'processando' : 'pronto'} • latência ${latency}ms`,
    });

  if (connected) {
    built.addFields(
      {
        name: '👤 Conta',
        value: `**${name}**\n\`${account.id}\``,
        inline: true,
      },
      {
        name: '💎 Orbs',
        value: `Saldo: **${apiBalance ?? historyOrbs}**\nA resgatar: **${claimableOrbs}**`,
        inline: true,
      },
      {
        name: '🎯 Quests',
        value: `Pendentes: **${stats?.valid ?? 0}**\nConcluídas: **${stats?.completed ?? 0}**`,
        inline: true,
      },
    );

    const total = stats?.total ?? 0;
    const percent = total > 0 ? Math.floor(((stats?.completed ?? 0) / total) * 100) : 0;
    built.addFields({
      name: '📈 Progresso geral',
      value: `${progressBar(percent)}\n\`${stats?.completed ?? 0}/${total}\` quests concluídas`,
    });
  } else {
    built.addFields(
      { name: '💎 Orbs', value: `Histórico: **${historyOrbs}**`, inline: true },
      { name: '⚙️ Sistema', value: `Paralelo: **${parallelLimit}**\nLatência: **${latency}ms**`, inline: true },
    );
  }

  const avatar = connected ? avatarUrl(account, 256) : client?.user?.displayAvatarURL?.({ size: 256 });
  if (avatar) built.setThumbnail(avatar);

  return built;
}

export function accountsEmbed({ accounts = [], activeId = null, busy = false } = {}) {
  const description = accounts.length
    ? accounts
        .map((account) => {
          const active = account.id === activeId;
          const marker = active ? '🟢' : '⚪';
          const label = displayName(account);
          return `${marker} **${label}**\n> \`${account.id}\`${active ? ' • **ativa**' : ''}`;
        })
        .join('\n\n')
    : 'Nenhuma conta salva ainda.\nUse **Adicionar conta** para começar.';

  return embed('👤 Suas contas', description, COLORS.brand)
    .addFields({
      name: '🔐 Segurança',
      value: 'Os tokens são salvos **criptografados** e restaurados ao reiniciar o bot.',
    })
    .setFooter({ text: busy ? 'Aguarde o processamento atual terminar' : `Total: ${accounts.length} conta(s)` });
}

export function accountComponents({ accounts = [], loggedIn = false, busy = false } = {}) {
  const rows = [];

  if (accounts.length > 0) {
    const options = accounts.slice(0, 25).map((account) => ({
      label: displayName(account).slice(0, 100),
      description: `${account.id}${account.active ? ' • ativa' : ''}`.slice(0, 100),
      value: account.id,
      default: account.active,
    }));

    rows.push(
      new ActionRowBuilder().addComponents(
        new StringSelectMenuBuilder()
          .setCustomId('mo:account_switch')
          .setPlaceholder('Trocar de conta...')
          .setDisabled(busy)
          .addOptions(options),
      ),
    );
  }

  rows.push(
    new ActionRowBuilder().addComponents(
      button('mo:login', 'Adicionar conta', ButtonStyle.Primary, { emoji: '➕' }),
      button('mo:account_remove', 'Remover conta', ButtonStyle.Danger, {
        emoji: '🗑️',
        disabled: busy || accounts.length === 0,
      }),
      button('mo:logout', 'Desconectar', ButtonStyle.Secondary, {
        emoji: '🚪',
        disabled: !loggedIn,
      }),
      button('mo:panel', 'Voltar', ButtonStyle.Secondary, { emoji: '⬅️' }),
    ),
  );

  return rows;
}

export function accountRemoveRow(accounts = []) {
  const options = accounts.slice(0, 25).map((account) => ({
    label: displayName(account).slice(0, 100),
    description: `${account.id}${account.active ? ' • ativa' : ''}`.slice(0, 100),
    value: account.id,
  }));

  return new ActionRowBuilder().addComponents(
    new StringSelectMenuBuilder()
      .setCustomId('mo:account_remove_select')
      .setPlaceholder('Escolha a conta para remover...')
      .addOptions(options),
  );
}

export function questSelectEmbed(quests = []) {
  return embed(
    '🎯 Escolher quest',
    [
      `**${quests.length}** quest(s) disponíveis.`,
      'Selecione uma abaixo para processar individualmente.',
      '',
      '> Use **Auto-Quest** no painel para processar todas de uma vez.',
    ].join('\n'),
    COLORS.info,
  ).setFooter({ text: 'Monsters Orbs • seleção de quest' });
}

export function questSelectRow(quests) {
  const options = quests.slice(0, 25).map((quest) => {
    const reward = quest.rewardAmount > 0 ? ` • +${quest.rewardAmount} Orbs` : '';
    const target = quest.taskTarget > 0 ? formatDuration(quest.taskTarget) : '?';
    return {
      label: `${taskEmoji(quest.taskType)} ${quest.name}`.slice(0, 100),
      description:
        `${quest.taskType ?? '??'} • ${formatDuration(quest.progressValue())}/${target}${reward}`.slice(
          0,
          100,
        ),
      value: quest.id,
    };
  });

  return new ActionRowBuilder().addComponents(
    new StringSelectMenuBuilder()
      .setCustomId('mo:choose')
      .setPlaceholder('Selecione uma quest...')
      .addOptions(options),
  );
}

export function questSummaryEmbed(quest, index, total, success) {
  const title = success ? '✅ Quest concluída' : '⚠️ Quest não concluída';
  const color = success ? COLORS.success : COLORS.danger;
  const target = quest.taskTarget > 0 ? quest.taskTarget : 0;

  const built = embed(
    title,
    [
      `### ${taskEmoji(quest.taskType)} ${quest.name}`,
      `**Tipo:** \`${quest.taskType ?? 'desconhecido'}\``,
      `**Progresso:** \`${quest.progressValue()}/${target}\``,
      quest.rewardAmount > 0 ? `**Recompensa:** \`+${quest.rewardAmount}\` 💎` : '',
    ]
      .filter(Boolean)
      .join('\n'),
    color,
  ).setFooter({ text: `Monsters Orbs • ${index}/${total}` });

  if (quest.heroImage?.startsWith('https://')) built.setImage(quest.heroImage);
  return built;
}

export function balanceEmbed(store, options = {}) {
  const { apiBalance = null, claimableOrbs = 0, pending = 0 } = options;

  const built = embed(
    '💎 Seu saldo',
    '### Orbs\nAcompanhe seus ganhos e recompensas disponíveis.',
    COLORS.orb,
  ).addFields(
    { name: '💰 Saldo histórico', value: `\`${store.totalOrbs}\` 💎`, inline: true },
    { name: '🎁 A resgatar agora', value: `\`${claimableOrbs}\` 💎`, inline: true },
    { name: '🎯 Quests pendentes', value: `\`${pending}\``, inline: true },
    { name: '🏅 Quests pagas', value: `\`${store.missionsPaid}\``, inline: true },
  );

  if (apiBalance !== null) {
    built.addFields({ name: '📡 Saldo na API', value: `\`${apiBalance}\` 💎`, inline: true });
  }

  const recent = store.recent(8);
  if (recent.length === 0) {
    built.addFields({ name: '🧾 Últimos ganhos', value: '> Nenhum ganho registrado ainda.' });
  } else {
    const lines = recent.map(
      (item) => `> \`+${item.orbs}\` 💎 — ${String(item.quest ?? '').slice(0, 45)}`,
    );
    built.addFields({ name: '🧾 Últimos ganhos', value: lines.join('\n').slice(0, 1024) });
  }

  return built.setFooter({ text: 'Monsters Orbs • histórico local' });
}

export function progressOverviewEmbed(stats, quests = []) {
  const built = embed(
    '📊 Seu progresso',
    [
      `**Pendentes:** \`${stats.valid}\` • **Concluídas:** \`${stats.completed}\` • **Resgatáveis:** \`${stats.claimable}\``,
    ].join('\n'),
    COLORS.accent,
  );

  if (quests.length > 0) {
    const details = quests
      .slice(0, 8)
      .map(
        (quest) =>
          `${taskEmoji(quest.taskType)} **${quest.name.slice(0, 45)}**\n> ${progressBar(quest.percent, 12)}`,
      )
      .join('\n');
    built.addFields({ name: '🎯 Quests pendentes', value: details.slice(0, 1024) });
  } else {
    built.addFields({ name: '🎉 Tudo em dia', value: '> Nenhuma quest pendente no momento.' });
  }

  return built;
}

export function welcomeEmbed(client) {
  const built = embed(
    '✨ Monsters Orbs',
    [
      '### Automatize suas quests do Discord',
      '> Complete missões, resgate Orbs e acompanhe tudo sem complicação.',
      '',
      '**O que o bot faz**',
      '🎯 Processa quests automaticamente',
      '💎 Resgata suas recompensas em Orbs',
      '🔐 Salva suas contas com segurança',
      '⚡ Acelera várias quests em paralelo',
      '📊 Mostra progresso, saldo e histórico',
      '',
      'Clique no botão abaixo para abrir o seu painel pessoal.',
    ].join('\n'),
    COLORS.brand,
  ).setFooter({ text: 'Painel privado • só você verá seus dados' });

  if (client?.user?.displayAvatarURL) {
    built.setThumbnail(client.user.displayAvatarURL({ size: 256 }));
  }
  return built;
}

export function welcomeComponents() {
  return [
    new ActionRowBuilder().addComponents(
      button('mo:open', 'Usar agora', ButtonStyle.Primary, { emoji: '🚀' }),
      button('mo:tutorial', 'Tutorial', ButtonStyle.Secondary, { emoji: '📚' }),
    ),
  ];
}

export const TUTORIAL_PAGES = [
  {
    title: '📚 Tutorial • 1/6',
    description: [
      '### 👋 Bem-vindo ao Monsters Orbs',
      'Este bot automatiza quests do Discord para você ganhar **Orbs**.',
      '',
      '**O que você vai precisar**',
      '• Discord no PC (app ou navegador)',
      '• Sua conta logada',
      '• Alguns minutos para a primeira configuração',
      '',
      '> Use **◀ Anterior** e **Próximo ▶** para navegar.',
    ].join('\n'),
  },
  {
    title: '📚 Tutorial • 2/6',
    description: [
      '### 🤖 O que o bot faz',
      '**🚀 Auto-Quest** — processa todas as quests disponíveis',
      '**🎯 Escolher quest** — processa uma quest específica',
      '**⚡ Turbo** — processa várias quests em paralelo',
      '**💎 Saldo** — mostra ganhos e recompensas a resgatar',
      '**📊 Progresso** — acompanha o andamento das quests',
      '**🔐 Contas** — salva suas contas com segurança',
      '',
      '> Tudo isso direto pelo painel, sem comandos complicados.',
    ].join('\n'),
  },
  {
    title: '📚 Tutorial • 3/6',
    description: [
      '### 🔑 Como pegar seu token',
      'O token funciona como uma "senha" da sua conta. **Nunca compartilhe.**',
      '',
      '1. Abra o Discord no **PC** e faça login.',
      '2. Pressione **Ctrl + Shift + I** para abrir o DevTools.',
      '3. Clique na aba **Console**.',
      '4. Cole o código abaixo e aperte **Enter**:',
      '```js',
      "(webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()",
      '```',
      '5. Copie o texto entre aspas que aparecer.',
      '',
      '> Se o Discord bloquear a colagem, digite **allow pasting** e dê Enter.',
    ].join('\n'),
  },
  {
    title: '📚 Tutorial • 4/6',
    description: [
      '### 🔗 Conectando sua conta',
      '1. Use o comando **/painel**.',
      '2. Clique em **🚀 Usar agora**.',
      '3. Clique em **➕ Conectar conta**.',
      '4. Cole o token que você copiou.',
      '5. Envie e pronto!',
      '',
      '**Sua conta fica salva** e é reconectada automaticamente quando o bot reiniciar.',
      '',
      '> Você pode adicionar quantas contas quiser na aba **Contas**.',
    ].join('\n'),
  },
  {
    title: '📚 Tutorial • 5/6',
    description: [
      '### 🎮 Usando o painel',
      '**🚀 Auto-Quest** — começa todas as quests na fila',
      '**🎯 Escolher quest** — escolhe uma quest específica',
      '**⚡ Turbo** — modo rápido com várias quests juntas',
      '**🛑 Cancelar** — para o processamento atual',
      '**🔄 Atualizar** — atualiza os dados do painel',
      '',
      'Os resultados de cada quest chegam por **DM**.',
    ].join('\n'),
  },
  {
    title: '📚 Tutorial • 6/6',
    description: [
      '### 🛡️ Segurança e dicas',
      '• Seus tokens são salvos **criptografados** no computador.',
      '• **Nunca** compartilhe seu token com ninguém.',
      '• Se trocar a senha do Discord, o token expira: remova a conta e conecte de novo.',
      '• Use o bot por sua conta e risco.',
      '',
      '**Tudo pronto!** Clique em **🚀 Abrir painel** para começar.',
    ].join('\n'),
  },
];

export function tutorialEmbed(page = 0, client = null) {
  const index = Math.max(0, Math.min(TUTORIAL_PAGES.length - 1, Number(page) || 0));
  const data = TUTORIAL_PAGES[index];
  const built = embed(data.title, data.description, COLORS.accent).setFooter({
    text: `Monsters Orbs • tutorial • página ${index + 1} de ${TUTORIAL_PAGES.length}`,
  });
  if (client?.user?.displayAvatarURL) {
    built.setThumbnail(client.user.displayAvatarURL({ size: 128 }));
  }
  return built;
}

export function tutorialComponents(page = 0) {
  const index = Math.max(0, Math.min(TUTORIAL_PAGES.length - 1, Number(page) || 0));
  const last = TUTORIAL_PAGES.length - 1;

  const rowOne = new ActionRowBuilder().addComponents(
    button('mo:tut:' + Math.max(0, index - 1), 'Anterior', ButtonStyle.Secondary, {
      emoji: '◀️',
      disabled: index === 0,
    }),
    button('mo:tut:info', `Página ${index + 1}/${TUTORIAL_PAGES.length}`, ButtonStyle.Secondary, {
      disabled: true,
    }),
    button('mo:tut:' + Math.min(last, index + 1), 'Próximo', ButtonStyle.Secondary, {
      emoji: '▶️',
      disabled: index === last,
    }),
  );

  const rowTwo = new ActionRowBuilder().addComponents(
    button('mo:open', 'Abrir painel', ButtonStyle.Primary, { emoji: '🚀' }),
    button('mo:close', 'Fechar', ButtonStyle.Secondary, { emoji: '✖️' }),
  );

  return [rowOne, rowTwo];
}

export function statusEmbed({ sessions, parallelLimit, totalOrbs, stats = null }) {
  const built = embed(
    '⚙️ Status do sistema',
    '### Monsters Orbs\nVisão geral do bot em tempo real.',
    COLORS.info,
  ).addFields(
    { name: '🟢 Sessões ativas', value: `\`${sessions}\``, inline: true },
    { name: '⚡ Paralelo', value: `\`${parallelLimit}\``, inline: true },
    { name: '💎 Saldo global', value: `\`${totalOrbs}\``, inline: true },
  );

  if (stats) {
    built.addFields(
      { name: '🎯 Quests na sua conta', value: `\`${stats.total}\``, inline: true },
      { name: '✅ Concluídas', value: `\`${stats.completed}\``, inline: true },
      { name: '💰 Resgatáveis', value: `\`${stats.claimable}\``, inline: true },
    );
  }

  return built.setFooter({ text: 'Monsters Orbs • status' });
}

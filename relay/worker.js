/**
 * Посредник между Telegram и GitHub Actions.
 *
 * Зачем он нужен: Telegram умеет слать вебхуки на любой HTTPS-адрес, но
 * не умеет добавлять к ним заголовок Authorization, а GitHub без него
 * workflow не запустит. Этот воркер принимает сообщение от Telegram,
 * проверяет его и вызывает GitHub API с нужным токеном.
 *
 * Текст команды передаётся в inputs запуска, потому что при включённом
 * вебхуке метод getUpdates перестаёт работать — забрать сообщение
 * обычным опросом скрипт уже не сможет.
 *
 * Переменные окружения (задаются в настройках воркера):
 *   WEBHOOK_SECRET    — произвольная строка, её же передать в setWebhook
 *   TELEGRAM_CHAT_ID  — ваш chat_id, команды от других игнорируются
 *   GITHUB_TOKEN      — токен с правом Actions: write
 *   GITHUB_REPO       — "логин/имя-репозитория"
 *   GITHUB_BRANCH     — ветка, по умолчанию main
 */

const WORKFLOW_FILE = "monitor.yml";

export default {
  async fetch(request, env) {
    // Telegram шлёт только POST. На GET отвечаем, чтобы адрес можно было
    // открыть в браузере и убедиться, что воркер жив.
    if (request.method !== "POST") {
      return new Response("flight-watch relay: работает", { status: 200 });
    }

    // Telegram присылает секрет в заголовке, если он задан в setWebhook.
    // Без этой проверки любой, кто узнает адрес, сможет запускать workflow.
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }

    const message = update.message || update.edited_message;
    const text = message && message.text;
    const chatId = message && message.chat && String(message.chat.id);

    // Не текст (стикер, фото) или чужой чат — молча игнорируем.
    // Отвечать посторонним нельзя: так бот выдал бы своё существование.
    if (!text || !chatId || chatId !== String(env.TELEGRAM_CHAT_ID)) {
      return new Response("ok");
    }

    const url = `https://api.github.com/repos/${env.GITHUB_REPO}` +
                `/actions/workflows/${WORKFLOW_FILE}/dispatches`;

    let failure = null;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "flight-watch-relay",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: env.GITHUB_BRANCH || "main",
          inputs: {
            command: text.slice(0, 200),   // ограничение GitHub на длину input
            chat_id: chatId,
          },
        }),
      });

      if (!response.ok) {
        const body = await response.text();
        failure = `GitHub ответил ${response.status}. ${body.slice(0, 200)}`;
      }
    } catch (e) {
      failure = `Не достучался до GitHub: ${e.message}`;
    }

    // Ответ на вебхук может сам быть вызовом метода Telegram — так
    // подтверждение приходит мгновенно, без отдельного запроса.
    const reply = failure
      ? `⚠️ Не удалось запустить проверку.\n${failure}`
      : "⏳ Принял, запускаю…";

    return new Response(JSON.stringify({
      method: "sendMessage",
      chat_id: chatId,
      text: reply,
    }), { headers: { "Content-Type": "application/json" } });
  },
};

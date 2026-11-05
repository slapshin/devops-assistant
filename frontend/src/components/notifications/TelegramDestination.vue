<script setup lang="ts">
/** The chat a Telegram notification channel posts into: the bot's token, the chat, and an optional forum topic. */
import { computed, ref, watch } from "vue";
import { ApiError, type TelegramChatList } from "../../api/client";
import { useTelegramChats } from "../../api/queries";
import type { FieldErrors } from "../../lib/auth";
import type { TelegramDraft } from "../../lib/notifications";

const destination = defineModel<TelegramDraft>({ required: true });
const props = defineProps<{
  idPrefix: string;
  /** Prefix of the error keys, e.g. "destination". */
  field: string;
  errors: FieldErrors;
  /** The stored channel being edited, whose token "Find chats" uses while none is typed. */
  channelId: string | null;
}>();

const finder = useTelegramChats();
const found = ref<TelegramChatList | null>(null);
const findError = ref<string | null>(null);

const error = (name: string) => props.errors[`${props.field}.${name}`];
const errorId = (name: string) => `err-${props.idPrefix}-${name}`;
const keepsToken = computed(() => !!destination.value.botTokenSet && !destination.value.botToken);

// Chats found for one bot are not another bot's.
watch(
  () => destination.value.botToken,
  () => {
    found.value = null;
    findError.value = null;
  },
);

async function findChats() {
  findError.value = null;
  found.value = null;
  const token = destination.value.botToken.trim();
  if (!token && !(keepsToken.value && props.channelId)) {
    findError.value = "Enter the bot token first.";
    return;
  }
  try {
    found.value = await finder.mutateAsync(token ? { bot_token: token } : { channel_id: props.channelId });
  } catch (e) {
    findError.value = e instanceof ApiError ? (e.problem.detail ?? e.problem.title) : "Unexpected error";
  }
}

function pick(chatId: string) {
  destination.value.chatId = chatId;
  found.value = null;
}
</script>

<template>
  <div class="stack">
    <div class="field">
      <label :for="`${idPrefix}-token`">Bot token</label>
      <input
        :id="`${idPrefix}-token`"
        v-model="destination.botToken"
        type="password"
        autocomplete="new-password"
        :placeholder="keepsToken ? 'Stored — leave empty to keep' : '123456789:AA…'"
        :aria-invalid="!!error('bot_token')"
        :aria-describedby="error('bot_token') ? errorId('bot_token') : `${idPrefix}-token-hint`"
      >
      <p :id="`${idPrefix}-token-hint`" class="muted hint">
        Create a bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> and paste its token. Add the bot to
        the chat (as an administrator in channels) so it can post.
      </p>
      <p v-if="error('bot_token')" :id="errorId('bot_token')" class="field-error">{{ error("bot_token") }}</p>
    </div>

    <div class="field">
      <label :for="`${idPrefix}-chat`">Chat ID</label>
      <div class="row">
        <input
          :id="`${idPrefix}-chat`"
          v-model="destination.chatId"
          autocomplete="off"
          placeholder="-1001234567890 or @channel_name"
          :aria-invalid="!!error('chat_id')"
          :aria-describedby="error('chat_id') ? errorId('chat_id') : `${idPrefix}-chat-hint`"
        >
        <button type="button" :disabled="finder.isPending.value" @click="findChats">
          {{ finder.isPending.value ? "Looking…" : "Find chats" }}
        </button>
      </div>
      <p :id="`${idPrefix}-chat-hint`" class="muted hint">
        Send any message in the chat (or add the bot to it), then use <em>Find chats</em>: it lists chats the bot saw in the last 24 hours.
      </p>
      <p v-if="error('chat_id')" :id="errorId('chat_id')" class="field-error">{{ error("chat_id") }}</p>
      <p v-if="findError" role="alert" class="field-error">{{ findError }}</p>
      <div v-if="found" class="found" role="group" :aria-label="`Chats of @${found.bot_username ?? 'bot'}`">
        <p class="muted hint">
          <template v-if="found.items.length">Recent chats of @{{ found.bot_username }}:</template>
          <template v-else>@{{ found.bot_username }} has seen no chat recently. Send it a message (or add it to the group) and try again.</template>
        </p>
        <ul v-if="found.items.length" class="chats">
          <li v-for="chat in found.items" :key="chat.chat_id">
            <button type="button" class="link" @click="pick(chat.chat_id)">
              {{ chat.title }} <span class="muted">({{ chat.type }}{{ chat.is_forum ? ", topics" : "" }}, {{ chat.chat_id }})</span>
            </button>
          </li>
        </ul>
      </div>
    </div>

    <div class="field topic">
      <label :for="`${idPrefix}-topic`">Topic ID <span class="muted">(optional)</span></label>
      <input
        :id="`${idPrefix}-topic`"
        v-model="destination.threadId"
        inputmode="numeric"
        autocomplete="off"
        :aria-invalid="!!error('message_thread_id')"
        :aria-describedby="error('message_thread_id') ? errorId('message_thread_id') : `${idPrefix}-topic-hint`"
      >
      <p :id="`${idPrefix}-topic-hint`" class="muted hint">For groups with topics: the number at the end of a topic message link.</p>
      <p v-if="error('message_thread_id')" :id="errorId('message_thread_id')" class="field-error">{{ error("message_thread_id") }}</p>
    </div>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: 2px; }
.field label { font-weight: 500; }
.field .row input { flex: 1 1 240px; }
.hint { font-size: 0.85rem; margin: 0; }
.topic input { max-width: 10rem; }
.chats { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.chats .link { font-size: 0.9rem; text-align: left; }
.found { border-left: 3px solid var(--border); padding-left: calc(var(--space) * 1.5); margin-top: 4px; }
</style>

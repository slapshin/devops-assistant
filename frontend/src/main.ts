import { VueQueryPlugin } from "@tanstack/vue-query";
import { createApp } from "vue";
import App from "./App.vue";
import { makeRouter } from "./router";
import "./styles/tokens.css";

createApp(App).use(makeRouter()).use(VueQueryPlugin).mount("#root");

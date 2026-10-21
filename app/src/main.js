import { mount } from "svelte";

import App from "./App.svelte";
import { requestPersistence } from "./services/storage.js";
import { newVersion } from "./services/version.svelte.js";
import "./style.css";

newVersion.register();
// Unsent things should not vanish only because storage runs short.
void requestPersistence();

export default mount(App, { target: document.getElementById("app") });

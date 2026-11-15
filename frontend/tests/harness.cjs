// Loads src/AgentPanel.jsx exactly as the browser does (classic script, React global,
// window.AgentPanel export) inside a jsdom page.
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const esbuild = require("esbuild");

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = require("react");
const testing = require("@testing-library/react");

const source = fs.readFileSync(path.join(__dirname, "..", "src", "AgentPanel.jsx"), "utf8");
const { code } = esbuild.transformSync(source, { loader: "jsx" });
new Function("React", "window", code)(React, dom.window);

// Node's own File is used for uploads because Node's FormData only accepts its own Blob types.
module.exports = { React, ...testing, AgentPanel: dom.window.AgentPanel, File: globalThis.File };

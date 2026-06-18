import { createSdkMcpServer } from "@anthropic-ai/claude-code";
import { topologyTools } from "./tools/topology.js";
import { spaceTools } from "./tools/spaces.js";
import { datasourceTools } from "./tools/datasource.js";
import { scenarioTools } from "./tools/scenarios.js";
import { investigationTools } from "./tools/investigation.js";
import { agentChatTools } from "./tools/agent_chat.js";
import { securityTools } from "./tools/security.js";
import { skillTools } from "./tools/skills.js";
import { knowledgeTools } from "./tools/knowledge.js";

export const overviewMcpServer = createSdkMcpServer({
  name: "overview-app",
  version: "1.0.0",
  tools: [
    ...topologyTools,
    ...spaceTools,
    ...datasourceTools,
    ...scenarioTools,
    ...investigationTools,
    ...agentChatTools,
    ...securityTools,
    ...skillTools,
    ...knowledgeTools,
  ],
});

# Related Work Expansion

This file records sources verified for the TDSC related-work expansion.
Only verified sources should be copied into `paper/tdsc/references.bib`.

| Key | Title | Authors / Org | Year | URL / DOI | Why relevant | Verified |
|---|---|---|---:|---|---|---|
| `rfc6749` | The OAuth 2.0 Authorization Framework | D. Hardt / IETF | 2012 | https://www.rfc-editor.org/info/rfc6749 / https://doi.org/10.17487/RFC6749 | Baseline delegated authorization model for web APIs. | yes |
| `nist2019abac` | Guide to Attribute Based Access Control (ABAC) Definition and Considerations | Hu et al. / NIST | 2019 | https://doi.org/10.6028/NIST.SP.800-162 | Defines ABAC as authorization over subject, object, operation, and environmental attributes. | yes |
| `oasis2013xacml` | eXtensible Access Control Markup Language (XACML) Version 3.0 | OASIS XACML TC | 2013 | https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html | Standard policy language and request/response model for attribute-based authorization. | yes |
| `dennis1966programming` | Programming Semantics for Multiprogrammed Computations | Dennis and Van Horn | 1966 | https://doi.org/10.1145/365230.365252 | Foundational capability-based protection reference. | yes |
| `miller2003capability` | Capability Myths Demolished | Miller, Yee, Shapiro | 2003 | https://classpages.cselabs.umn.edu/Fall-2021/csci5271/papers/SRL2003-02.pdf | Clarifies capability security properties and misconceptions. | yes |
| `agrawal2002hippocratic` | Hippocratic Databases | Agrawal, Kiernan, Srikant, Xu | 2002 | https://www.vldb.org/conf/2002/S05P02.pdf | Database privacy architecture based on purpose and obligations. | yes |
| `byun2008purpose` | Purpose Based Access Control for Privacy Protection in Relational Database Systems | Byun and Li | 2008 | https://doi.org/10.1007/s00778-006-0023-0 | Purpose-aware relational access control closest to task-purpose enforcement. | yes |
| `postgresql2026rowsecurity` | Row Security Policies | PostgreSQL Global Development Group | 2026 | https://www.postgresql.org/docs/current/ddl-rowsecurity.html | Database-native row filtering baseline. | yes |
| `oracle2026vpd` | Using Oracle Virtual Private Database to Control Data Access | Oracle | 2026 | https://docs.oracle.com/en/database/oracle/oracle-database/19/dbseg/using-oracle-vpd-to-control-data-access.html | Database row/column policy enforcement baseline. | yes |
| `oracle2026deepdatasecuritydocs` | What Is Oracle Deep Data Security | Oracle | 2026 | https://docs.oracle.com/en/database/oracle/oracle-database/26/ddscg/what-is-oracle-deep-data-security.html | Contemporary database-enforced access control for agentic AI. | yes |
| `oracle2026deepdatasecuritybrief` | Oracle Deep Data Security Technical Brief | Oracle | 2026 | https://www.oracle.com/a/ocom/docs/security/deep-data-security-technical-brief.pdf | Detailed positioning of Oracle DDS for agents, analytics, and applications. | yes |
| `boyd2004sqlrand` | SQLrand: Preventing SQL Injection Attacks | Boyd and Keromytis | 2004 | https://doi.org/10.1007/978-3-540-24852-1_21 | SQL parser-level defense against injected query structure. | yes |
| `su2006essence` | The Essence of Command Injection Attacks in Web Applications | Su and Wassermann | 2006 | https://doi.org/10.1145/1111037.1111070 | SQLCHECK-style parse-tree validation for command injection. | yes |
| `kleinberg2000auditing` | Auditing Boolean Attributes | Kleinberg, Papadimitriou, Raghavan | 2000 | https://doi.org/10.1145/335168.335210 | Formal query auditing for statistical databases. | yes |
| `nabar2006robustness` | Towards Robustness in Query Auditing | Nabar, Marthi, Kenthapadi, Mishra, Motwani | 2006 | https://www.vldb.org/conf/2006/p151-nabar.pdf | Online query auditing for streams of aggregate queries. | yes |
| `alneyadi2016dlps` | A Survey on Data Leakage Prevention Systems | Alneyadi, Sithirasenan, Muthukkumarasamy | 2016 | https://doi.org/10.1016/j.jnca.2016.01.008 | DLP/data leakage prevention baseline for exfiltration control. | yes |
| `nist2026dlpglossary` | Data Loss Prevention | NIST CSRC | 2026 | https://csrc.nist.gov/glossary/term/data_loss_prevention | Defines DLP capabilities around data in use, motion, and rest. | yes |
| `myers1997difc` | A Decentralized Model for Information Flow Control | Myers and Liskov | 1997 | https://www.cs.cornell.edu/andru/papers/iflow-sosp97/paper.html | Foundational decentralized IFC model. | yes |
| `costa2025ifcagents` | Securing AI Agents with Information-Flow Control | Costa et al. | 2025 | https://arxiv.org/abs/2505.23643 | Applies IFC to AI agents and prompt-injection threats. | yes |
| `greshake2023indirect` | Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection | Greshake et al. | 2023 | https://doi.org/10.1145/3605764.3623985 | Shows indirect prompt injection risks for tool-using LLM applications. | yes |
| `liu2023promptinjection` | Prompt Injection Attack against LLM-integrated Applications | Liu et al. | 2023 | https://arxiv.org/abs/2306.05499 | Prompt-injection attack framework for LLM-integrated applications. | yes |
| `owasp2025llm01` | LLM01:2025 Prompt Injection | OWASP GenAI Security Project | 2025 | https://genai.owasp.org/llmrisk/llm01-prompt-injection/ | Practitioner risk taxonomy for prompt injection. | yes |
| `south2025authenticateddelegation` | Authenticated Delegation and Authorized AI Agents | South et al. | 2025 | https://arxiv.org/abs/2501.09674 | Agent delegation, accountability, and scoped credentials. | yes |
| `sharma2026pauth` | PAuth - Precise Task-Scoped Authorization For Agents | Sharma et al. | 2026 | https://arxiv.org/abs/2603.17170 | Task-scoped operation authorization for agents. | yes |
| `zigmond2020shilldb` | Fine-Grained, Language-Based Access Control for Database-Backed Applications | Zigmond, Chong, Dimoulas, Moore | 2020 | https://doi.org/10.22152/programming-journal.org/2020/4/3 | Language-level database capabilities and contracts for component-specific DB access. | yes |
| `bichhawat2020estrela` | Contextual and Granular Policy Enforcement in Database-backed Applications | Bichhawat, Fredrikson, Yang, Trehan | 2020 | https://doi.org/10.1145/3320269.3384759 | API-contextual pre/post query policy enforcement for database-backed applications. | yes |
| `chen2025picachv` | PICACHV: Formally Verified Data Use Policy Enforcement for Secure Data Analytics | Chen et al. | 2025 | https://www.usenix.org/conference/usenixsecurity25/presentation/chen-haobin | Formally verified relational-algebra monitor for analytics data-use policy enforcement. | yes |
| `tonnarelli2026dataproductmcp` | Data Product MCP: Chat with your Enterprise Data | Tonnarelli et al. | 2026 | https://arxiv.org/abs/2601.08687 | Governed enterprise data-product access through MCP. | yes |
| `mcp2025specification` | Model Context Protocol Specification | Model Context Protocol | 2025 | https://modelcontextprotocol.io/specification/2025-06-18 | Tool/data connectivity protocol used by agents. | yes |
| `pang2019zanzibar` | Zanzibar: Google's Consistent, Global Authorization System | Pang et al. | 2019 | https://www.usenix.org/conference/atc19/presentation/pang | Large-scale relationship-based authorization baseline. | yes |
| `dwork2014algorithmic` | The Algorithmic Foundations of Differential Privacy | Dwork and Roth | 2014 | https://doi.org/10.1561/0400000042 | Clarifies what formal DP guarantees require. | yes |
| `adam1989securitycontrol` | Security-Control Methods for Statistical Databases: A Comparative Study | Adam and Wortmann | 1989 | https://doi.org/10.1145/76894.76895 | Classical inference-control survey for statistical databases. | yes |

Summary: 32 verified references are now available for the manuscript. The
expansion should emphasize that SessionBound is not claiming novelty in OAuth,
ABAC, capabilities, RLS/VPD, IFC, DLP, database privacy, language-level database
contracts, contextual policy frameworks, or verified data-use monitors; its
contribution is the task-approved, budgeted, receipt-bearing database session for
agent-generated SQL.

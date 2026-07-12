# Security Policy

Phlox combines model-generated actions, local files, external model providers, code
execution, authentication, and multi-user data. Security reports—especially those involving
an isolation or authorization boundary—are taken seriously.

## Supported versions

Phlox is currently a pre-1.0 project. Security fixes are made on a best-effort basis for the
most recent published release and the current `main` branch.

| Version | Security support |
| --- | --- |
| Most recent release | Supported |
| Current `main` branch | Supported for the next release |
| Older releases and branches | Not supported; upgrade to the latest release |

Security fixes may include breaking changes when that is necessary to protect users. Watch
the repository's releases and security advisories, and keep Phlox and its container images
up to date.

## Reporting a vulnerability

**Do not open a public issue, discussion, or pull request for a suspected vulnerability.**

Use GitHub's private vulnerability reporting form:

**[Report a Phlox vulnerability privately](https://github.com/robert-mcdermott/phlox/security/advisories/new)**

If GitHub private reporting is unavailable, email
[robert.c.mcdermott@gmail.com](mailto:robert.c.mcdermott@gmail.com) with the subject
`[PHLOX SECURITY]`. In the initial email, provide only enough information to establish
contact; do not send credentials, private user data, or a weaponized exploit over
unencrypted email.

Include as much of the following as is practical:

- The affected release, commit, deployment method, and relevant configuration.
- The vulnerability type, affected component, and security boundary that is crossed.
- Reproduction steps or a minimal proof of concept.
- The demonstrated and potential impact, including prerequisites and required privileges.
- Relevant logs or screenshots after removing tokens, passwords, personal data, model
  provider credentials, and other secrets.
- Any suggested mitigation or fix, if known.
- Whether you intend to publish the finding and your preferred disclosure timeline.
- How you would like to be credited, or whether you prefer to remain anonymous.

If a repository or release credential appears to be exposed, report it immediately and do
not attempt to use it beyond what is necessary to confirm that it is a credential.

## What to expect

These are response targets rather than guaranteed service-level agreements:

- Acknowledgment within three business days.
- Initial validation and severity assessment within seven business days.
- A status update at least every fourteen days while a confirmed issue remains open.

After triage, the maintainer will coordinate remediation and disclosure based on severity,
exploitability, affected users, and fix complexity. A confirmed issue may be handled through
a private GitHub security advisory, with a CVE requested when appropriate. Reporters who
follow this policy will be offered credit unless they request anonymity.

Please allow a reasonable remediation period before publishing details. If you believe
users face immediate harm or the issue is being actively exploited, say so clearly in the
report so an accelerated disclosure plan can be discussed.

## Vulnerabilities in scope

Examples include, but are not limited to:

- Authentication, session, password-reset, API-key, or role-escalation flaws.
- Cross-user access to conversations, files, documents, memories, assistants, usage data,
  approvals, or administrative operations.
- Sandbox escapes or unintended access from agent-executed code to the Phlox host, host
  credentials, other users' workspaces, or container-control interfaces.
- Workspace path traversal, unsafe file upload or extraction, or arbitrary file access.
- SSRF protections that can be bypassed to reach private services, loopback interfaces,
  cloud metadata endpoints, or disallowed redirect targets.
- Secret exposure through logs, APIs, configuration responses, model context, generated
  artifacts, or sandbox environments.
- Injection or request-handling flaws that cross a documented trust boundary.
- Vulnerable dependencies when there is a credible, reachable impact in Phlox.
- Denial-of-service issues that are practical against a supported, properly configured
  deployment and have a material availability impact.

## Generally out of scope

The following are normally not vulnerabilities unless they can be used to cross a security
boundary or contradict a documented guarantee:

- Model hallucinations, undesirable model output, or prompt injection by itself.
- Actions a user explicitly approved, or permitted through agent-mode auto-approval, when
  the action stays within that user's authorized data and execution environment.
- Host access by the documented `local` sandbox runner. It is intentionally not an
  isolation boundary and is for trusted, single-user development only.
- Reports that only restate a dependency scanner result without showing that the affected
  code is present and reachable in Phlox.
- Missing hardening that has no demonstrated security impact, such as a header-only report.
- Self-XSS or attacks that require a victim to paste attacker-controlled code into a
  browser developer console.
- Social engineering, phishing, physical attacks, or attacks against third-party services.
- Unsupported versions, configurations that deliberately disable security controls, or
  deployments that ignore the documented production requirements.

The maintainer may still accept hardening recommendations through a normal public issue if
they do not reveal an exploitable weakness.

## Research guidelines and safe harbor

To keep research safe and eligible for coordinated handling:

- Test only systems and data you own or have explicit permission to test.
- Prefer a local, isolated Phlox deployment with synthetic data.
- Do not access, retain, alter, or disclose another person's data.
- Do not perform destructive testing, persistence, social engineering, denial of service,
  broad automated scanning, or third-party infrastructure attacks.
- Use the minimum access and data needed to demonstrate the issue, then stop testing.
- Preserve confidentiality until a coordinated disclosure date is agreed upon or the
  maintainer has had a reasonable opportunity to remediate the issue.
- Comply with applicable law and the terms of any third-party platform involved.

When research and disclosure are conducted in good faith and follow this policy, the Phlox
maintainer will not initiate legal action against the researcher for that work. If an action
was accidental, report it promptly so it can be addressed. This safe-harbor statement cannot
bind third parties or authorize testing of systems the maintainer does not own.

## Deployment security

Before deploying Phlox, review the production guidance in
[Authentication and authorization](docs/AUTH.md), [Sandboxing](docs/SANDBOX.md), and
[Deployment](docs/DEPLOYMENT.md). 

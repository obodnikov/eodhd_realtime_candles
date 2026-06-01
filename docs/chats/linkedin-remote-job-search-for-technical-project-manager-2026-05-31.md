# Claude Chat Export
**Generated: 2026-05-31 20:59:51**

---

👤 **USER:**
> # AGENTS.md instructions for /Users/eobomik/Documents/LinkedIn

<INSTRUCTIONS>
---
inclusion: always
---

# Confirm Before Action

This rule applies to ALL interactions. Kiro must never make changes without explicit user approval.

## Core Behavior

**NEVER immediately:**
- Edit or modify existing files
- Create new files
- Delete files
- Run commands that change state
- Execute write operations on external systems (JIRA, databases, APIs, etc.)

**ALWAYS first:**
1. Explain the situation or problem
2. Propose a clear solution with specifics (what files, what changes, what commands)
3. Wait for explicit user confirmation ("yes", "proceed", "do it", etc.)

## Allowed Without Confirmation

These read-only operations can proceed immediately:
- Reading files and directories
- Searching code and content
- Gathering context about the codebase
- Analyzing problems and errors
- Answering questions with explanations
- Web searches for information

## Response Pattern

When user asks for something that requires changes:

```
[Explain the situation/problem]

[Proposed solution:]
- What will be changed/created/deleted
- Which files affected
- What commands will run (if any)

Ready to proceed? (yes/no)
```

Only after receiving confirmation, execute the changes.

## Exception

If the user explicitly says "just do it", "go ahead without asking", or similar in their request, proceed directly. But default behavior is always: explain first, confirm, then act.
</INSTRUCTIONS>
<environment_context>
  <cwd>/Users/eobomik/Documents/LinkedIn</cwd>
  <shell>zsh</shell>
  <current_date>2026-05-31</current_date>
  <timezone>Europe/Budapest</timezone>
</environment_context>

---

👤 **USER:**
> Используй mcp server linkedin в данном чате. 
Прочитай мой профиль в linkedIn olga-akopyan-8762b54
Я сейчас ищу работу и только удаленную работу. Я нахожусь в Будапеште но могу сделав ИП работать на любую компанию в любой стране. Язык английский и русский. Поищи мне вакансии подходящие под мой профиль

Да, займусь через LinkedIn MCP: сначала прочитаю профиль и извлеку ключевые критерии, затем поищу удаленные вакансии с учетом языка, локации и формата контракта. Это только чтение и поиск, изменений нигде не вношу.

LinkedIn MCP открыл окно входа и сообщил, что активной сессии нет. Пожалуйста, войдите в LinkedIn в открывшемся браузерном окне; после этого я повторю чтение профиля и поиск вакансий.

Нужен вход в LinkedIn: MCP-сервер не нашел активную сессию и открыл окно логина. Войдите там в свой аккаунт LinkedIn, затем напишите мне “готово”, и я продолжу: прочитаю профиль `olga-akopyan-8762b54` и подберу удаленные вакансии под ваш опыт, языки и формат работы через ИП.

---

👤 **USER:**
> Я залогинился под своим профилем

Отлично, пробую снова прочитать профиль. После этого сделаю несколько поисков по удаленным вакансиям, а не один широкий запрос, чтобы не потерять роли из-за разных названий должностей.

Профиль прочитан. По нему видно сильное направление: Technical/IT Project Manager, Program/Delivery, support/service delivery, cloud/Kubernetes/AWS, IoT; поэтому ищу не только “Project Manager”, но и смежные названия, где ваш опыт может пройти фильтры рекрутера.

Я уже вижу несколько хороших кластеров: EMEA remote, EU remote, Hungary remote и contract. Теперь открываю детали у самых релевантных вакансий, потому что заголовок часто обманывает: важны стек, seniority, язык и ограничения по стране/праву на работу.

Есть несколько кандидатов, но я отсекаю роли с явными стоп-факторами: например обязательный немецкий/французский или слишком инженерная позиция. Сейчас отдельно проверю вакансии, где русский может быть преимуществом, потому что это может резко повысить шанс отклика.

Прочитала профиль и поискала remote-вакансии через LinkedIn MCP. Под ваш профиль лучше всего ложатся: Technical Project Manager, Senior Project Manager, Service/Delivery Manager, Implementation Manager, Integration Manager, Program Manager. Поиск делала по remote, EMEA/EU/Hungary, full-time и contract.

**Лучшие совпадения**
1. [OpenVPN — Senior Project Manager / Technical Project Manager](https://www.linkedin.com/jobs/view/4419067408/)  
   EU remote, contract. Очень хорошее совпадение: SaaS/cloud/networking/security, distributed engineering teams, Agile/SDLC. Ваш Cisco + GE Healthcare + cloud/Kubernetes здесь выглядят сильными.

2. [SiiRA — Project Manager](https://www.linkedin.com/jobs/view/4421603379/)  
   EMEA remote, contract. Хороший широкий вариант: software/digital delivery, Agile ceremonies, distributed teams, Jira/Asana/Trello. Подходит под ИП/contract.

3. [Ashby — Manager of Dedicated Implementations, EMEA](https://www.linkedin.com/jobs/view/4408948858/)  
   EU remote, full-time, €90K–€120K. Сильная роль, если хотите customer-facing implementation/professional services. Ваш high-touch/customer/stakeholder background хорошо подходит.

4. [INFUSE — Senior Project Manager, Remote Contract](https://www.linkedin.com/jobs/view/4420613739/)  
   Budapest remote, Easy Apply. Хороший practical-match: PM + business analysis/product/process improvement, English upper-intermediate+. Вакансия выглядит менее технической, зато шанс отклика может быть выше.

5. [Hays — Cutover Manager, Contracting](https://www.linkedin.com/jobs/view/4420984008/)  
   Hungary remote, B2B contracting, English. Контракт на 2026 с возможным продолжением до 2028. Подходит по формату ИП, но нужен опыт cutover/ERP/MES; если он есть хотя бы частично, стоит пробовать.

6. [Envision Pharma Group — Technical Project Manager](https://www.linkedin.com/jobs/view/4412932239/)  
   Hungary remote, full-time. Плюс: healthcare/pharma контекст и technical delivery. Минус: просят SaaS/CRM типа Salesforce/Veeva, data/AI/ML знания, поэтому это “хороший, но с адаптацией CV”.

7. [Amaris Consulting — Senior Project Manager](https://www.linkedin.com/jobs/view/4412274392/)  
   EU remote, Easy Apply, freelance/contract. Сильный контрактный вариант, но нужен Finance Transformation/SAP background. Можно подаваться, если в GE/Cisco были enterprise transformation/SAP-adjacent проекты.

8. [GE HealthCare — M365 SharePoint & OneDrive Technical Product Manager](https://www.linkedin.com/jobs/view/4411676844/)  
   Budapest remote. Интересно из-за вашего опыта в GE HealthCare и stakeholder/service ownership, но роль требует глубокий SharePoint/OneDrive/M365 admin background. Я бы рассматривала как stretch.

**Отсеяла / низкий приоритет**
- Penta Technical Program Manager: требует fluent French + English.  
- Deutsche Telekom Service Delivery Manager: немецкий B1-B2 и remote только внутри Венгрии; можно, но язык стоп-фактор.  
- Onapsis Customer Success Manager: German required и страны Germany/Spain/UK/Netherlands.  
- Coinspaid Golang Engineer: хороший English/Russian remote, но это инженерная Golang-вакансия, не под ваш PM-профиль.  
- n8n Senior/Staff PM: это Product Manager 0→1 AI Labs, не Project Manager; высокая конкуренция и другой профиль.

Важное наблюдение: в вашем LinkedIn раздел `Languages` пустой, хотя для поиска это критично. Я бы добавила `English` и `Russian` в профиль, а в headline/summary усилила фразы: `Remote Technical Project Manager`, `B2B contractor`, `EMEA`, `Cloud / SaaS / Healthcare / Telecom`, `English and Russian`.


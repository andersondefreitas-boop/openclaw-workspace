# AnestesiaApp

Sistema web para gestão de consultas pré-anestésicas.

## Rotas

- `/` — Dashboard com métricas e próximas consultas
- `/patients` — cadastro e gerenciamento de pacientes
- `/schedule` — agenda mensal de procedimentos
- `/consultation/:appointmentId` — ficha completa de consulta pré-anestésica
- `/reports` — gráficos e estatísticas clínicas

## Entidades locais

- `Patient` — dados cadastrais, contatos e consentimento LGPD
- `Appointment` — agendamentos cirúrgicos
- `Consultation` — anamnese, exames, escores, conduta e consentimento
- `AccessLog` — log local de acesso LGPD

Os dados são persistidos no `localStorage` do navegador para facilitar testes e demonstração.

## Desenvolvimento

```bash
npm install
npm run dev
npm run build
```

# Rotic HRM - employee feed for the company ERP

The HRM is the master record for staff. The ERP reads from it on a schedule; it never writes to it.

## Address and key
- List: `GET https://hrm.roticaluminium.com/api/integrations/erp/employees/`
- One person: `GET https://hrm.roticaluminium.com/api/integrations/erp/employees/<staff number>/`
- Every request carries the key HR gives you: `Authorization: Api-Key <key>` (or `X-Api-Key: <key>`).
- HTTPS only. A wrong, switched-off or out-of-place key gets `401`. The key may be limited to your server's internet address.

## Getting only what changed
1. First run: call the list with no `updated_since`, follow `next_page` until it is `null`, and save `server_time` from the first page.
2. Every run after that (every 15 minutes is plenty): call with `updated_since=<the saved server_time>`; you get only people added or changed since then (new hires, edits, and exits - `status` becomes `inactive` and `exit_date` is filled). Save the new `server_time`.
3. Match people on `staff_number`. It never changes.

Optional: `page_size` (up to 500, default 200), `page`, `status` (`active`, `inactive`, `suspended`, `terminated`).

## Example
```
curl -H "Authorization: Api-Key rhrm_xxxxxxxx" \
  "https://hrm.roticaluminium.com/api/integrations/erp/employees/?updated_since=2026-10-23T09:00:00%2B01:00"
```
```json
{
  "count": 1, "page": 1, "page_size": 200, "next_page": null,
  "server_time": "2026-10-23T09:15:02.481Z",
  "results": [{
    "staff_number": "001372", "first_name": "Ezigbo", "middle_name": "Chukwuebuka", "last_name": "Fidelis",
    "full_name": "Ezigbo Fidelis Chukwuebuka", "department": "Scrap", "position": "Factory Worker",
    "employment_type": "casual", "employment_category": "staff", "status": "active", "gender": "male",
    "employment_date": "2026-10-09", "exit_date": null, "phone": null, "email": null,
    "updated_at": "2026-10-23T08:40:11.120Z"
  }]
}
```
Salary and bank details are not sent.

const assert = require('node:assert/strict');
const {previousMonthKey, filterRows, totals, groupPurchases, projectionEstimate} = require('../finance_bot/ui/dashboard.js');
assert.equal(previousMonthKey('2026-09'), '2026-08');
assert.equal(previousMonthKey('2026-01'), '2025-12');
const rows = [
  {id:1, monthKey:'2026-08', kind:'income', user:'Ariel', amountCents:100, description:'Nómina', isFixed:true},
  {id:2, monthKey:'2026-08', kind:'income', user:'Dahiana', amountCents:900, description:'Nómina', isFixed:true},
  {id:3, monthKey:'2026-09', kind:'income', user:'Ariel', amountCents:200, description:'Nómina', isFixed:true},
];
const filters = {month:'2026-09', user:'Ariel', search:'nomina'};
assert.equal(totals(filterRows(rows, filters)).income, 200);
assert.equal(totals(filterRows(rows, filters, previousMonthKey(filters.month))).income, 100);
const purchaseRows = [
  {id:1,receipt:'ticket.jpg',dateIso:'2026-09-02',kind:'expense',user:'Ariel',amountCents:250},
  {id:2,receipt:'ticket.jpg',dateIso:'2026-09-02',kind:'expense',user:'Ariel',amountCents:50},
  {id:3,receipt:'ticket.jpg',dateIso:'2026-09-03',kind:'expense',user:'Ariel',amountCents:100},
];
const groups = groupPurchases(purchaseRows);
assert.equal(groups.length,2); assert.equal(groups[0].amountCents,300); assert.equal(groups[0].children.length,2);
assert.equal(projectionEstimate({actualIncomeCents:130000,actualExpenseCents:40000,pendingIncomeCents:5000,pendingExpenseCents:10000}),85000);
console.log('Calendar, filter parity, accent search, purchase grouping and closing estimate passed.');

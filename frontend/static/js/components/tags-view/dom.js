export const findList = (root = document) =>
  root.querySelector?.("#tags-view-list") || document.getElementById("tags-view-list");

export const findDetail = (root = document) =>
  root.querySelector?.("#tags-view-detail") || document.getElementById("tags-view-detail");

export const findSidebar = (root = document) =>
  root.querySelector?.(".tags-view__sidebar-fixed") ||
  document.querySelector(".tags-view__sidebar-fixed");

export const findListBody = (root = document) =>
  root.querySelector?.(".tags-view__list-body") || document.querySelector(".tags-view__list-body");

export const findIndexData = (root = document) =>
  root.querySelector?.("#tags-index-data") || document.getElementById("tags-index-data");

export const findEntriesList = (root = document) =>
  findDetail(root)?.querySelector?.("[data-tags-view-entries]") ||
  document.querySelector("#tags-view-detail [data-tags-view-entries]");

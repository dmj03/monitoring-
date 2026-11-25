import { request } from "./client";
import type { ClusterList } from "./types";

// Clusters are computed per request over the whole dataset. There is no
// GET /clusters/{id}: cluster_ref is assigned per detection run and is not a
// durable identifier, so cluster detail is rendered from this one response.
export const listClusters = () => request<ClusterList>("/clusters");

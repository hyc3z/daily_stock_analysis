import type { MarketReviewRegion } from '../types/analysis';

export type { MarketReviewRegion };

/** 六个股票市场；`both` 在服务端仍然只代表这六个。 */
export const STOCK_MARKET_REVIEW_REGIONS: readonly MarketReviewRegion[] = ['cn', 'hk', 'us', 'jp', 'kr', 'tw'];

/** 选择器展示顺序：股票六市场在前，虚拟货币作为可选的第七项。 */
export const MARKET_REVIEW_REGION_ORDER: readonly MarketReviewRegion[] = [
  ...STOCK_MARKET_REVIEW_REGIONS,
  'crypto',
];

/**
 * 序列化为服务端接受的 `region` 字符串。
 *
 * - 只勾选六个股票市场时仍返回 `both`，保持与旧版一致的语义和请求体积；
 * - 勾选虚拟货币时必须显式列出（服务端的 `both` 不含 crypto）。
 */
export function serializeMarketReviewRegions(regions: readonly MarketReviewRegion[]): string {
  const ordered = MARKET_REVIEW_REGION_ORDER.filter((region) => regions.includes(region));
  const includesCrypto = ordered.includes('crypto');
  const stockRegions = ordered.filter((region) => region !== 'crypto');
  if (!includesCrypto && stockRegions.length === STOCK_MARKET_REVIEW_REGIONS.length) {
    return 'both';
  }
  return ordered.join(',');
}

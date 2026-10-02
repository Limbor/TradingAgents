import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { TrendingDown, TrendingUp, X } from "lucide-react";
import type { Holding } from "@/api/client";
import { displayNameOf } from "@/components/common/StockName";

interface Props {
  holding: Holding;
  action: "add" | "reduce";
  submitting?: boolean;
  error?: string | null;
  initialQuantity?: number;
  initialPrice?: number;
  onConfirm: (action: "add" | "reduce", quantity: number, price: number) => void;
  onClose: () => void;
}

function fmt(n: number, digits = 2) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(n);
}

function cnASellQuantityError(symbol: string, current: number, sell: number): string | null {
  if (!/\.(SH|SZ|BJ)$/i.test(symbol)) return null;
  if (!Number.isInteger(current) || !Number.isInteger(sell)) {
    return "A 股卖出数量必须是整数股。";
  }
  if (sell <= 0 || sell > current || sell === current) return null;
  const remainder = current % 100;
  const valid = remainder === 0 ? sell % 100 === 0 : sell % 100 === 0 || sell % 100 === remainder;
  if (valid) return null;
  if (current <= 100) {
    return `当前仅持有 ${fmt(current)} 股，不支持部分减仓；如需卖出必须一次性清仓 ${fmt(current)} 股。`;
  }
  return remainder
    ? `部分卖出须为 100 股整手，或一次性包含全部 ${remainder} 股零股余数。`
    : "部分卖出数量必须是 100 股的整数倍。";
}

/** 加仓 / 减仓 弹窗:按一笔交易调整持仓,实时预览新成本(加仓)或已实现盈亏(减仓)。 */
export function AdjustPositionDialog({ holding, action, submitting, error, initialQuantity, initialPrice, onConfirm, onClose }: Props) {
  const isAdd = action === "add";
  const [quantity, setQuantity] = useState(initialQuantity && initialQuantity > 0 ? String(initialQuantity) : "");
  const [price, setPrice] = useState(
    initialPrice && initialPrice > 0
      ? String(initialPrice)
      : holding.current_price != null ? String(holding.current_price) : String(holding.avg_cost),
  );

  const qty = Number(quantity);
  const px = Number(price);
  const qtyValid = Number.isFinite(qty) && qty > 0;
  const pxValid = Number.isFinite(px) && px > 0;
  const overSell = !isAdd && qtyValid && qty > holding.quantity;
  const lotError = !isAdd && qtyValid && !overSell
    ? cnASellQuantityError(holding.symbol, holding.quantity, qty)
    : null;
  const canSubmit = qtyValid && pxValid && !overSell && !lotError && !submitting;

  const oldQty = holding.quantity;
  const oldAvg = holding.avg_cost;
  const newQty = canSubmit ? (isAdd ? oldQty + qty : oldQty - qty) : undefined;
  const newAvg = isAdd && newQty !== undefined ? (oldQty * oldAvg + qty * px) / newQty : undefined;
  const realized = !isAdd && canSubmit ? qty * (px - oldAvg) : undefined;
  const closed = !isAdd && newQty !== undefined && newQty <= 0;
  const title = isAdd ? "加仓" : "减仓";

  return (
    <Dialog.Root open onOpenChange={(o) => { if (!o && !submitting) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,420px)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-ui-strong bg-ui-panel p-5 shadow-xl">
          <div className="flex items-center justify-between">
            <Dialog.Title className="flex items-center gap-2 text-sm font-semibold text-ui-ink">
              {isAdd ? <TrendingUp className="h-4 w-4 text-ui-accent" /> : <TrendingDown className="h-4 w-4 text-ui-warning" />}
              {title} · {displayNameOf(holding.name, holding.symbol)}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded p-1 text-ui-faint hover:bg-ui-hover hover:text-ui-body" disabled={submitting} aria-label="关闭">
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="mt-2 text-xs text-ui-faint">
            当前持仓 <span className="font-mono text-ui-body">{fmt(oldQty)}</span> 股 · 成本 <span className="font-mono text-ui-body">{fmt(oldAvg)}</span>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-ui-muted">{isAdd ? "买入数量" : "卖出数量"}</span>
              <input
                type="number"
                min={0}
                step={1}
                value={quantity}
                placeholder="100"
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-lg border border-ui-strong bg-ui-subtle px-3 py-2 text-sm text-ui-ink outline-none focus:border-ui-accent"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-ui-muted">{isAdd ? "买入价" : "卖出价"}</span>
              <input
                type="number"
                min={0}
                step="any"
                value={price}
                placeholder="0.00"
                onChange={(e) => setPrice(e.target.value)}
                className="w-full rounded-lg border border-ui-strong bg-ui-subtle px-3 py-2 text-sm text-ui-ink outline-none focus:border-ui-accent"
              />
            </label>
          </div>

          {overSell && (
            <div className="mt-3 rounded border border-ui-danger/30 bg-ui-danger/10 px-3 py-2 text-xs text-ui-danger">
              卖出数量超过当前持仓({fmt(oldQty)} 股)。
            </div>
          )}
          {lotError && (
            <div className="mt-3 rounded border border-ui-warning/30 bg-ui-warning/10 px-3 py-2 text-xs text-ui-warning">
              {lotError}
            </div>
          )}
          {error && (
            <div className="mt-3 rounded border border-ui-danger/30 bg-ui-danger/10 px-3 py-2 text-xs text-ui-danger">
              {error}
            </div>
          )}

          {newQty !== undefined && (
            <div className="mt-3 rounded-lg border border-ui-strong bg-ui-subtle px-3 py-2 text-xs text-ui-body">
              {isAdd ? (
                <div>
                  加仓后 <span className="font-mono text-ui-ink">{fmt(newQty)}</span> 股 · 新成本{" "}
                  <span className="font-mono text-ui-accent">{fmt(newAvg!)}</span>
                </div>
              ) : (
                <div>
                  减仓后 <span className="font-mono text-ui-ink">{fmt(newQty)}</span> 股
                  {closed && <span className="ml-2 text-ui-warning">(清仓)</span>}
                  {!closed && realized !== undefined && (
                    <span className={`ml-2 font-mono ${realized >= 0 ? "text-ui-success" : "text-ui-danger"}`}>
                      已实现 {realized >= 0 ? "+" : ""}{fmt(realized)}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={submitting}
              className="rounded-lg border border-ui-strong px-3 py-2 text-sm text-ui-body transition hover:bg-ui-hover disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={() => canSubmit && onConfirm(action, qty, px)}
              disabled={!canSubmit}
              className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                isAdd ? "bg-ui-accent text-ui-onAccent hover:bg-ui-accent" : "bg-ui-warning text-ui-onWarning hover:bg-ui-warning"
              }`}
            >
              {submitting ? "提交中..." : `确认${title}`}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

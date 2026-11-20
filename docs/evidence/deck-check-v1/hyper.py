from math import comb
N = 98  # 100 minus two commanders in the command zone
for L in (38, 36, 34, 33, 28):
    p = lambda k: comb(L, k) * comb(N - L, 7 - k) / comb(N, 7)
    keep = sum(p(k) for k in range(2, 6))
    ge3 = sum(p(k) for k in range(3, 8))
    # >= 4 lands seen by turn 4 on the draw (7 + 4 = 11 cards)
    q = lambda k: comb(L, k) * comb(N - L, 11 - k) / comb(N, 11)
    t4 = sum(q(k) for k in range(4, 12))
    print(f"lands={L}: P(2-5 in opener)={keep:.3f}  P(>=3 in opener)={ge3:.3f}  P(>=4 lands by T4 on draw)={t4:.3f}")

class TreeNode(object):
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
class Node:
    def __init__(self, x, next=None, random=None):
        self.val = int(x)
        self.next = next
        self.random = random
class Solution:
    def findAnagrams(self, s, p):
        if len(p)>len(s):
            return []
        occp = {}
        occs = {}
        for c in p:
            occp[c] =occp.get(c,0)+1
        n = len(s)
        m = len(p)
        for i in range(0,m):
            occs[s[i]] = occs.get(s[i],0)+1
        if occp == occs:
            res.append(0)
        i = m
        res = []
        while i<n:
            occs[s[i]] = occs.get(s[i],0)+1
            occs[s[i-m]]-=1
            if occs[s[i-m]] == 0:
                del occs[s[i-m]]
            if occp == occs:
                res.append(i-m)
            i+=1
    def minSumOfLengths(self, arr, target):
        n = len(arr)
        res = [] 

        s = [0] * (n + 1)
        for i in range(1, n + 1):
            s[i] = s[i - 1] + arr[i - 1]

        h = {}
        for i in range(0, n + 1):
            h[s[i]] = i

        for i in range(0, n + 1):
            if target + s[i] in h:
                j = h[target + s[i]]
                res.append((i, j, j - i))

        if len(res) < 2:
            return -1

        ans = n + 1
        min_len_before = n + 1
        p = 0

        for i in range(len(res)):
            left, right, length = res[i]

            while p < i and res[p][1] <= left:
                if res[p][2] < min_len_before:
                    min_len_before = res[p][2]
                p += 1
            if min_len_before != n + 1:
                ans = min(ans, min_len_before + length)

        return -1 if ans == n + 1 else ans
    def maxProduct(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        dpmax = nums[:]
        dpmin = nums[:]
        for i in range(1,len(nums)):
            dpmax[i] = max(max(dpmax[i-1]*nums[i],nums[i]),dpmin[i-1]*nums[i])
            dpmin[i] = min(min(dpmin[i-1]*nums[i],nums[i]),dpmax[i-1]*nums[i])
        return max(dpmax)


    def dfs(self,grid,i,j):
        if i<0 or i>=len(grid) or j<0 or j>=len(grid[0]):
            return 0
        if grid[i][j] ==0 or grid[i][j] ==-1:
            return 0
        else:
            temp = grid[i][j]
            grid[i][j] =-1 
            t =  temp + self.dfs(grid,i+1,j)+self.dfs(grid,i-1,j) + self.dfs(grid,i,j+1) +self.dfs(grid,i,j-1)
            return t


    def countIslands(self, grid, k):
        """
        :type grid: List[List[int]]
        :type k: int
        :rtype: int
        """
        res = []
        for i in range(len(grid)):
            for j in range(len(grid[0])):
                if grid[i][j]!=0 and grid[i][j] !=-1 and  0<=i<=len(grid)-1 and 0<=j<=len(grid[0])-1:
                    res.append(self.dfs(grid,i,j))
        count = 0
        for num in res:
            if 0 == (num -(num/k)*k):
                count +=1
        return count

    def lowestCommonAncestor(self, root, p, q):
        """
        :type root: TreeNode
        :type p: TreeNode
        :type q: TreeNode
        :rtype: TreeNode
        """
        if root is p or root is q:
            return root
        if not root :
            return None

        left = self.lowestCommonAncestor(root.left,p,q)
        right = self.lowestCommonAncestor(root.right,p,q)
        if left and right:
            return root    

        if left:
            return left
        return right    
    def pathSum(self, root, targetSum):
        s = []
        def preorder(root,s):



        
        


        





        
def main():
    print(46515)

if __name__ == "__main__":
    main()
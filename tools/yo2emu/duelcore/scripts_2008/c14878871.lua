--レスキューキャット
--Rescue Cat (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "You can send this face-up card to the Graveyard to Special Summon 2
--Level 3 or lower Beast-Type monsters from your Deck. Those monsters are destroyed during the End Phase."
--Diferencias con CardScripts (errata moderna): (1) fuera 'but their effects are negated' (los dos monstruos conservan sus
--efectos hasta que los destruye la End Phase); (2) fuera 'You can only use this effect of "Rescue Cat" once per turn'.
local s,id=GetID()
function s.initial_effect(c)
	--Special Summon 2 Level 3 or lower Beast monsters from your Deck
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_SPECIAL_SUMMON)
	e1:SetType(EFFECT_TYPE_IGNITION)
	e1:SetRange(LOCATION_MZONE)
	e1:SetCost(Cost.SelfToGrave)
	e1:SetTarget(s.sptg)
	e1:SetOperation(s.spop)
	c:RegisterEffect(e1)
end
function s.spfilter(c,e,tp)
	return c:IsLevelBelow(3) and c:IsRace(RACE_BEAST) and c:IsCanBeSpecialSummoned(e,0,tp,false,false)
end
function s.sptg(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return Duel.GetMZoneCount(tp,e:GetHandler())>1 and not Duel.IsPlayerAffectedByEffect(tp,CARD_BLUEEYES_SPIRIT)
		and Duel.IsExistingMatchingCard(s.spfilter,tp,LOCATION_DECK,0,2,nil,e,tp) end
	Duel.SetOperationInfo(0,CATEGORY_SPECIAL_SUMMON,nil,2,tp,LOCATION_DECK)
end
function s.spop(e,tp,eg,ep,ev,re,r,rp)
	if Duel.GetLocationCount(tp,LOCATION_MZONE)<2 or Duel.IsPlayerAffectedByEffect(tp,CARD_BLUEEYES_SPIRIT) then return end
	Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_SPSUMMON)
	local sg=Duel.SelectMatchingCard(tp,s.spfilter,tp,LOCATION_DECK,0,2,2,nil,e,tp)
	if #sg~=2 then return end
	local c=e:GetHandler()
	local fid=c:GetFieldID()
	for tc in sg:Iter() do
		Duel.SpecialSummonStep(tc,0,tp,tp,false,false,POS_FACEUP)
		tc:RegisterFlagEffect(id,RESET_EVENT|RESETS_STANDARD,0,1,fid)
	end
	Duel.SpecialSummonComplete()
	sg:KeepAlive()
	--Destroy them during the End Phase
	local e3=Effect.CreateEffect(c)
	e3:SetType(EFFECT_TYPE_FIELD+EFFECT_TYPE_CONTINUOUS)
	e3:SetProperty(EFFECT_FLAG_IGNORE_IMMUNE)
	e3:SetCode(EVENT_PHASE+PHASE_END)
	e3:SetCountLimit(1)
	e3:SetLabel(fid)
	e3:SetLabelObject(sg)
	e3:SetCondition(s.descon)
	e3:SetOperation(s.desop)
	Duel.RegisterEffect(e3,tp)
end
function s.desfilter(c,fid)
	return c:GetFlagEffectLabel(id)==fid
end
function s.descon(e,tp,eg,ep,ev,re,r,rp)
	local g=e:GetLabelObject()
	if not g:IsExists(s.desfilter,1,nil,e:GetLabel()) then
		g:DeleteGroup()
		e:Reset()
		return false
	else return true end
end
function s.desop(e,tp,eg,ep,ev,re,r,rp)
	local g=e:GetLabelObject()
	local tg=g:Filter(s.desfilter,nil,e:GetLabel())
	Duel.Destroy(tg,REASON_EFFECT)
end